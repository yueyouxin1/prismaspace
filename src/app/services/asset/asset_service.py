import logging
from pathlib import Path
from typing import List, Optional, Sequence

from sqlalchemy import select

from app.core.context import AppContext
from app.core.storage.factory import get_storage_provider
from app.dao.asset.asset_dao import AssetDao
from app.dao.asset.folder_dao import AssetFolderDao
from app.dao.workspace.workspace_dao import WorkspaceDao
from app.models import Asset, AssetIntelligence, AssetStatus, AssetType, IntelligenceStatus, User, Workspace
from app.schemas.asset.asset_schemas import (
    AssetConfirm,
    AssetCreate,
    AssetRead,
    AssetUpdate,
    AssetUploadTicket,
    PaginatedAssetsResponse,
)
from app.services.asset.utils import detect_asset_type, generate_storage_key
from app.services.base_service import BaseService
from app.services.exceptions import NotFoundError, ServiceException
from app.utils.id_generator import generate_uuid

logger = logging.getLogger(__name__)


class AssetService(BaseService):
    """Manage end-to-end asset upload and asset CRUD for workspace scope."""

    def __init__(self, context: AppContext):
        self.db = context.db
        self.context = context
        self.dao = AssetDao(context.db)
        self.folder_dao = AssetFolderDao(context.db)
        self.workspace_dao = WorkspaceDao(context.db)
        self.storage = get_storage_provider()

    async def _resolve_workspace(self, workspace_uuid: str) -> Workspace:
        workspace = await self.workspace_dao.get_by_uuid(workspace_uuid)
        if not workspace:
            raise NotFoundError("Workspace not found.")
        return workspace

    async def _resolve_folder(
        self,
        workspace: Workspace,
        *,
        folder_uuid: Optional[str],
        folder_id: Optional[int],
    ):
        if not folder_uuid and folder_id is None:
            return None

        folder = None
        if folder_uuid:
            folder = await self.folder_dao.get_active_by_uuid(folder_uuid, withs=["parent"])
        elif folder_id is not None:
            raw = await self.folder_dao.get_by_pk(folder_id, withs=["parent"])
            if raw and not raw.is_deleted:
                folder = raw

        if not folder:
            raise NotFoundError("Target folder not found.")
        if folder.workspace_id != workspace.id:
            raise ServiceException("Target folder belongs to a different workspace.")
        return folder

    def _collect_descendant_folder_ids(self, all_folders: Sequence, root_folder_id: int) -> List[int]:
        children_by_parent: dict[int, list[int]] = {}
        for folder in all_folders:
            if folder.parent_id is None:
                continue
            children_by_parent.setdefault(folder.parent_id, []).append(folder.id)

        result = [root_folder_id]
        stack = [root_folder_id]
        while stack:
            parent_id = stack.pop()
            for child_id in children_by_parent.get(parent_id, []):
                result.append(child_id)
                stack.append(child_id)
        return result

    async def create_upload_ticket(self, workspace_uuid: str, params: AssetCreate, actor: User) -> AssetUploadTicket:
        workspace = await self._resolve_workspace(workspace_uuid)
        await self.context.perm_evaluator.ensure_can(["workspace:asset:create"], target=workspace)

        folder = await self._resolve_folder(
            workspace,
            folder_uuid=params.folder_uuid,
            folder_id=params.folder_id,
        )

        asset_uuid = generate_uuid()
        physical_key = generate_storage_key("workspace", workspace.id, asset_uuid, params.filename)
        ticket = self.storage.generate_upload_ticket(
            key=physical_key,
            mime_type=params.mime_type,
            max_size_bytes=params.size_bytes,
        )

        return AssetUploadTicket(
            asset_uuid=asset_uuid,
            upload_url=ticket.upload_url,
            form_data=ticket.form_data,
            provider=ticket.provider,
            upload_key=physical_key,
            folder_uuid=folder.uuid if folder else None,
        )

    async def confirm_upload(self, params: AssetConfirm, actor: User) -> AssetRead:
        workspace = await self._resolve_workspace(params.workspace_uuid)
        await self.context.perm_evaluator.ensure_can(["workspace:asset:create"], target=workspace)

        existing_asset = await self.dao.get_by_uuid(params.asset_uuid, withs=["folder", "intelligence"])
        if existing_asset and existing_asset.status == AssetStatus.ACTIVE and not existing_asset.is_deleted:
            return AssetRead.model_validate(existing_asset)

        folder = await self._resolve_folder(
            workspace,
            folder_uuid=params.folder_uuid,
            folder_id=params.folder_id,
        )

        try:
            metadata = await self.storage.get_object_metadata(params.upload_key)
        except FileNotFoundError as exc:
            raise NotFoundError("Physical file not found. Upload may have failed.") from exc

        content_hash = metadata.hash_str
        asset_type = detect_asset_type(metadata.content_type)

        ws_config = workspace.asset_config or {}
        enable_ai = bool(ws_config.get("enable_ai_processing", False))
        if params.force_ai_processing is not None:
            enable_ai = params.force_ai_processing
        if asset_type == AssetType.OTHER:
            enable_ai = False

        should_trigger_worker = False

        async with self.db.begin_nested():
            stmt = (
                select(AssetIntelligence)
                .where(AssetIntelligence.content_hash == content_hash)
                .with_for_update()
            )
            intelligence = (await self.db.execute(stmt)).scalar_one_or_none()

            if not intelligence:
                intelligence = AssetIntelligence(
                    content_hash=content_hash,
                    status=IntelligenceStatus.PENDING,
                )
                self.db.add(intelligence)
                should_trigger_worker = enable_ai
            elif enable_ai and intelligence.status in {IntelligenceStatus.PENDING, IntelligenceStatus.FAILED}:
                intelligence.status = IntelligenceStatus.PENDING
                should_trigger_worker = True

            final_name = params.name.strip() if params.name else Path(params.upload_key).name
            new_asset = Asset(
                uuid=params.asset_uuid,
                workspace_id=workspace.id,
                folder_id=folder.id if folder else None,
                creator_id=actor.id,
                storage_provider=self.storage.name,
                real_name=params.upload_key,
                url=self.storage.get_public_url(params.upload_key),
                content_hash=content_hash,
                name=final_name,
                size=metadata.size,
                mime_type=metadata.content_type,
                type=asset_type,
                status=AssetStatus.ACTIVE,
            )
            self.db.add(new_asset)
            await self.db.flush()

        if should_trigger_worker and self.context.arq_pool:
            await self.context.arq_pool.enqueue_job(
                "process_asset_intelligence_task",
                asset_uuid=params.asset_uuid,
                user_uuid=actor.uuid,
            )

        final_asset = await self.dao.get_active_by_uuid(params.asset_uuid, withs=["folder", "intelligence"])
        if not final_asset:
            raise NotFoundError("Asset not found after confirm.")
        return AssetRead.model_validate(final_asset)

    async def list_assets(
        self,
        *,
        workspace_uuid: str,
        actor: User,
        folder_uuid: Optional[str] = None,
        folder_id: Optional[int] = None,
        include_subfolders: bool = False,
        asset_type: Optional[AssetType] = None,
        keyword: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
    ) -> PaginatedAssetsResponse:
        workspace = await self._resolve_workspace(workspace_uuid)
        await self.context.perm_evaluator.ensure_can(["workspace:asset:read"], target=workspace)

        folder = await self._resolve_folder(
            workspace,
            folder_uuid=folder_uuid,
            folder_id=folder_id,
        )

        folder_ids = None
        if folder:
            if include_subfolders:
                all_folders = await self.folder_dao.list_by_workspace(workspace.id)
                folder_ids = self._collect_descendant_folder_ids(all_folders, folder.id)
            else:
                folder_ids = [folder.id]

        items, total = await self.dao.list_by_workspace(
            workspace.id,
            folder_ids=folder_ids,
            asset_type=asset_type,
            keyword=keyword,
            page=page,
            limit=limit,
        )
        return PaginatedAssetsResponse(
            items=[AssetRead.model_validate(item) for item in items],
            total=total,
            page=page,
            limit=limit,
        )

    async def get_asset(self, asset_uuid: str, actor: User) -> AssetRead:
        asset = await self.dao.get_active_by_uuid(asset_uuid, withs=["workspace", "folder", "intelligence"])
        if not asset:
            raise NotFoundError("Asset not found.")

        await self.context.perm_evaluator.ensure_can(["workspace:asset:read"], target=asset.workspace)
        return AssetRead.model_validate(asset)

    async def update_asset(self, asset_uuid: str, update_data: AssetUpdate, actor: User) -> AssetRead:
        asset = await self.dao.get_active_by_uuid(asset_uuid, withs=["workspace", "folder", "intelligence"])
        if not asset:
            raise NotFoundError("Asset not found.")
        await self.context.perm_evaluator.ensure_can(["workspace:asset:update"], target=asset.workspace)

        if update_data.name is not None:
            name = update_data.name.strip()
            if not name:
                raise ServiceException("Asset name cannot be empty.")
            asset.name = name

        if "folder_uuid" in update_data.model_fields_set or "folder_id" in update_data.model_fields_set:
            if update_data.folder_uuid is None and update_data.folder_id is None:
                asset.folder_id = None
                asset.folder = None
            else:
                folder = await self._resolve_folder(
                    asset.workspace,
                    folder_uuid=update_data.folder_uuid,
                    folder_id=update_data.folder_id,
                )
                asset.folder_id = folder.id if folder else None
                asset.folder = folder

        await self.db.flush()
        latest = await self.dao.get_active_by_uuid(asset_uuid, withs=["folder", "intelligence"])
        if not latest:
            raise NotFoundError("Asset not found after update.")
        return AssetRead.model_validate(latest)

    async def delete_asset(self, asset_uuid: str, actor: User) -> None:
        asset = await self.dao.get_active_by_uuid(asset_uuid, withs=["workspace"])
        if not asset:
            raise NotFoundError("Asset not found.")

        await self.context.perm_evaluator.ensure_can(["workspace:asset:delete"], target=asset.workspace)
        await self.dao.soft_delete(asset.uuid)
        await self.db.flush()

        if self.context.arq_pool:
            await self.context.arq_pool.enqueue_job("physical_delete_asset_task", asset_uuid=asset.uuid)
