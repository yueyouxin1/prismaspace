import logging
from typing import List, Optional
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.context import AppContext
from app.core.storage.factory import get_storage_provider
from app.models import User, Workspace, Asset, AssetType, AssetStatus, AssetIntelligence, IntelligenceStatus
from app.dao.asset.asset_dao import AssetDao
from app.dao.asset.folder_dao import AssetFolderDao
from app.dao.product.feature_dao import FeatureDao
from app.dao.workspace.workspace_dao import WorkspaceDao
from app.schemas.asset.asset_schemas import AssetCreate, AssetUpdate, AssetUploadTicket, AssetRead, AssetConfirm
from app.services.base_service import BaseService
from app.services.exceptions import NotFoundError, PermissionDeniedError, ServiceException
from app.services.billing.context import BillingContext
from app.services.asset.utils import generate_storage_key, detect_asset_type
from app.utils.id_generator import generate_uuid

logger = logging.getLogger(__name__)

class AssetService(BaseService):
    """
    [Service Layer] Manages the full lifecycle of digital assets.
    Handles storage negotiation, database tracking, billing integration, and worker dispatch.
    """
    
    def __init__(self, context: AppContext):
        self.db = context.db
        self.context = context
        self.dao = AssetDao(context.db)
        self.folder_dao = AssetFolderDao(context.db)
        self.workspace_dao = WorkspaceDao(context.db)
        self.feature_dao = FeatureDao(context.db)
        self.storage = get_storage_provider()

    # ==========================================================================
    # 1. Upload Flow (Reservation & Commit)
    # ==========================================================================

    async def create_upload_ticket(
        self,
        workspace_uuid: str,
        params: AssetCreate,
        actor: User
    ) -> AssetUploadTicket:
        """
        生成上传凭证。
        此阶段不创建 Asset 记录，只生成物理路径。Asset 记录在 Confirm 阶段创建。
        原因：防止大量未上传的垃圾数据占用 DB，且物理路径是确定的。
        """
        # 1. Load Workspace & Permissions
        workspace = await self.workspace_dao.get_by_uuid(workspace_uuid)
        if not workspace:
            raise NotFoundError("Workspace not found.")
        
        # 权限检查
        await self.context.perm_evaluator.ensure_can(["workspace:asset:create"], target=workspace)

        # 2. Check Storage Quota (Optional / Billing)
        # 作为基础设施，暂不计费

        # 3. Generate Physical Path (Isolation)
        # Path Rule: workspaces/{id}/assets/{yyyy}/{mm}/{uuid}.ext
        temp_uuid = generate_uuid() # 这个 UUID 将成为 Asset UUID
        
        physical_key = generate_storage_key(
            "workspace", workspace.id, temp_uuid, params.filename
        )

        # 4. Sign URL
        ticket = self.storage.generate_upload_ticket(
            key=physical_key,
            mime_type=params.mime_type,
            max_size_bytes=params.size_bytes
        )

        return AssetUploadTicket(
            asset_uuid=temp_uuid,
            upload_url=ticket.upload_url,
            form_data=ticket.form_data,
            provider=ticket.provider,
            upload_key=physical_key
        )

    async def confirm_upload(
        self, 
        params: AssetConfirm,
        actor: User
    ) -> AssetRead:
        """
        确认上传完成。
        1. 验证 OSS 文件存在并获取 Hash。
        2. 写入数据库 (Asset + AssetIntelligence)。
        3. 计费 (存储空间)。
        4. 触发 AI Worker (如果需要)。
        """
        asset_uuid = params.asset_uuid
        workspace_uuid = params.workspace_uuid
        workspace = await self.workspace_dao.get_by_uuid(workspace_uuid)
        if not workspace: raise NotFoundError("Workspace not found.")
        await self.context.perm_evaluator.ensure_can(["workspace:asset:create"], target=workspace)

        # Check if asset UUID already exists (Idempotency)
        existing_asset = await self.dao.get_by_uuid(asset_uuid)
        if existing_asset:
            # 如果已存在且是 Active 状态，直接返回（幂等）
            if existing_asset.status == AssetStatus.ACTIVE:
                return AssetRead.model_validate(existing_asset)
            # 否则可能是之前的失败尝试，允许覆盖逻辑继续

        # 1. 验证 OSS 文件
        upload_key = params.upload_key
        try:
            metadata = await self.storage.get_object_metadata(upload_key)
        except FileNotFoundError:
            raise NotFoundError("Physical file not found. Upload may have failed.")
        
        content_hash = metadata.hash_str
        asset_type = detect_asset_type(metadata.content_type)
        
        # 2. 判断是否启用智能处理
        # 优先级: 用户本次请求 > Workspace 配置 > 默认 False
        ws_config = workspace.asset_config or {}
        enable_ai = ws_config.get("enable_ai_processing", False)
        if params.force_ai_processing is not None:
            enable_ai = params.force_ai_processing
            
        # 某些类型不需要 AI (如 OTHER)
        if asset_type == "other":
            enable_ai = False

        should_trigger_worker = False

        # 3. 数据库事务
        async with self.db.begin_nested():
            # A. 处理 Intelligence (共享层)
            # 尝试获取现有的 Intelligence
            stmt = select(AssetIntelligence).where(AssetIntelligence.content_hash == content_hash).with_for_update()
            result = await self.db.execute(stmt)
            intelligence = result.scalar_one_or_none()

            if not intelligence:
                # 新内容：创建记录
                intelligence = AssetIntelligence(
                    content_hash=content_hash,
                    status=IntelligenceStatus.PENDING if enable_ai else IntelligenceStatus.PENDING, # 初始状态
                    # 如果不启用AI，这里的状态可以保持 PENDING 或者设为 SKIPPED?
                    # 为了简单，设为 PENDING，Worker 发现 enable_ai=False 就不跑了? 
                    # 不，Worker 是被显式触发的。
                    # 如果不启用 AI，我们在 DB 里留个空记录，状态为 PENDING (等待未来可能的处理)
                )
                self.db.add(intelligence)
                if enable_ai:
                    should_trigger_worker = True
            else:
                # 已存在：检查状态
                if enable_ai and intelligence.status == IntelligenceStatus.FAILED:
                    # 之前失败了，重试
                    intelligence.status = IntelligenceStatus.PENDING
                    should_trigger_worker = True
                elif enable_ai and intelligence.status == IntelligenceStatus.PENDING:
                    # 已经在处理中或等待处理，无需再次触发，或者触发以防万一丢消息
                    should_trigger_worker = True

            # B. 创建 Asset (逻辑层)
            # 确定文件名：优先用 params.name，否则从 upload_key 或元数据推断
            final_name = params.name or params.upload_key.split('/')[-1]

            new_asset = Asset(
                uuid=asset_uuid,
                workspace_id=workspace.id,
                # 默认放到根目录，如果 Ticket 阶段支持 Folder，这里需要透传 Folder ID
                # 简化起见，confirm 接口暂不接收 folder_id，后续通过 update 移动
                folder_id=None, 
                creator_id=actor.id,
                
                storage_provider=self.storage.__class__.__name__,
                real_name=upload_key,
                url=self.storage.get_public_url(upload_key),
                
                content_hash=content_hash,
                name=final_name,
                size=metadata.size,
                mime_type=metadata.content_type,
                type=asset_type,
                status=AssetStatus.ACTIVE
            )
            self.db.add(new_asset)
            
            # C. 计费: 存储配额 (TODO: 对接 BillingService)

        # 4. 事务提交后，触发 Worker
        if should_trigger_worker:
            await self.context.arq_pool.enqueue_job(
                'process_asset_intelligence_task', 
                asset_uuid=asset_uuid,
                user_uuid=actor.uuid
            )
            
        # 5. 返回结果 (需重新加载以获取 relationships)
        # 由于刚才在事务内，对象可能未绑定 Session，重新查询最稳妥
        final_asset = await self.dao.get_active_by_uuid(asset_uuid, withs=["intelligence"])
        response = AssetRead.model_validate(final_asset)
        response.ai_status = final_asset.intelligence.status
        response.ai_meta = final_asset.intelligence.meta
        return response

    # ==========================================================================
    # 2. Management (CRUD)
    # ==========================================================================

    async def delete_asset(self, asset_uuid: str, actor: User) -> None:
        """
        Soft deletes the asset DB record and queues physical deletion.
        """
        asset = await self.dao.get_active_by_uuid(asset_uuid, withs=["workspace"])
        if not asset:
            raise NotFoundError("Asset not found.")

        # Permissions
        await self.context.perm_evaluator.ensure_can(["workspace:asset:delete"], target=asset.workspace)

        # 1. Soft Delete DB
        await self.dao.soft_delete(asset.uuid)

        await self.db.flush()

        # 2. Queue Physical Delete (Robustness)
        if self.context.arq_pool:
            await self.context.arq_pool.enqueue_job(
                'physical_delete_asset_task',
                asset_uuid=asset.uuid
            )

    async def list_assets(
        self,
        workspace_uuid: str,
        actor: User,
        folder_id: Optional[int] = None,
        asset_type: Optional[AssetType] = None,
        page: int = 1,
        limit: int = 20
    ) -> List[AssetRead]:
        """
        Lists assets with filtering.
        """
        workspace = await self.workspace_dao.get_by_uuid(workspace_uuid)
        if not workspace: raise NotFoundError("Workspace not found.")
        await self.context.perm_evaluator.ensure_can(["workspace:asset:read"], target=workspace)

        assets = await self.dao.list_by_folder(
            workspace_id=workspace.id,
            folder_id=folder_id,
            asset_type=asset_type,
            page=page,
            limit=limit
        )
        return [AssetRead.model_validate(a) for a in assets]

    async def update_asset(self, asset_uuid: str, update_data: AssetUpdate, actor: User) -> AssetRead:
        asset = await self.dao.get_active_by_uuid(asset_uuid, withs=["workspace"])
        if not asset: raise NotFoundError("Asset not found")
        await self.context.perm_evaluator.ensure_can(["workspace:asset:update"], target=asset.workspace)

        if update_data.name:
            asset.name = update_data.name
        
        if update_data.folder_id is not None:
            folder = await self.folder_dao.get_by_pk(update_data.folder_id)
            
            if not folder or folder.is_deleted: # [增强] 检查 is_deleted
                raise NotFoundError("Target folder not found")
            
            # Ensure folder belongs to same workspace (Security Check)
            if folder.workspace_id != asset.workspace_id:
                raise ServiceException("Cannot move asset to a folder in a different workspace")
            
            asset.folder_id = folder.id

        await self.db.flush()
        final_asset = await self.dao.get_active_by_uuid(asset_uuid)
        return AssetRead.model_validate(final_asset)