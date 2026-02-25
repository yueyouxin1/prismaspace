# src/app/services/resource/uiapp/uiapp_service.py

import logging
from typing import Dict, Any, List, Optional
from pydantic import ValidationError

from app.core.context import AppContext
from app.models import User, Resource, VersionStatus
from app.models.resource.uiapp import UiApp, UiPage
from app.dao.resource.uiapp.uiapp_dao import UiAppDao, UiPageDao
from app.schemas.resource.uiapp.uiapp_schemas import (
    UiAppUpdate, UiAppMetadataRead, UiAppSchema, 
    UiPageCreate, UiPageUpdate, UiPageDetail, UiPageMeta
)
from app.schemas.resource.resource_schemas import InstancePublish
from app.services.resource.base.base_impl_service import register_service, ResourceImplementationService, ValidationResult, DependencyInfo
from app.services.resource.resource_ref_service import ResourceRefService
from app.services.resource.uiapp.dependency_extractor import DependencyExtractor
from app.services.exceptions import ServiceException, NotFoundError, PermissionDeniedError
from app.engine.model.llm import LLMTool, LLMToolFunction
from app.models.resource import ResourceRef

logger = logging.getLogger(__name__)

@register_service
class UiAppService(ResourceImplementationService):
    name: str = "uiapp"

    def __init__(self, context: AppContext):
        super().__init__(context)
        self.dao = UiAppDao(context.db)
        self.page_dao = UiPageDao(context.db)
        self.ref_service = ResourceRefService(context)
        self.extractor = DependencyExtractor()

    # ==========================================================================
    # 1. Page Level Operations (New Granular APIs)
    # ==========================================================================

    async def get_page_detail(self, app_uuid: str, page_uuid: str, actor: User) -> UiPageDetail:
        """获取单个页面的完整 DSL"""
        app = await self.get_by_uuid(app_uuid)
        if not app: raise NotFoundError("App not found")
        await self._check_execute_perm(app) # 读权限检查

        page = await self.page_dao.get_by_app_and_page_uuid(app.version_id, page_uuid)
        if not page: raise NotFoundError("Page not found")
        
        return UiPageDetail.model_validate(page)

    async def create_page(self, app_uuid: str, page_data: UiPageCreate, actor: User) -> UiPageMeta:
        """添加新页面"""
        app = await self._get_workspace_app_for_edit(app_uuid, actor)
        
        # 检查 page_uuid 和 path 唯一性
        existing = await self.page_dao.get_by_app_and_page_uuid(app.version_id, page_data.page_uuid)
        if existing: raise ServiceException(f"Page UUID {page_data.page_uuid} already exists")

        new_page = UiPage(
            app_version_id=app.version_id,
            **page_data.model_dump()
        )
        self.db.add(new_page)
        await self.db.flush()
        
        # 触发引用同步
        await self._sync_all_dependencies(app)
        
        return UiPageMeta.model_validate(new_page)

    async def update_page(self, app_uuid: str, page_uuid: str, update_data: UiPageUpdate, actor: User) -> UiPageDetail:
        """更新页面 (DSL 或 元数据)"""
        app = await self._get_workspace_app_for_edit(app_uuid, actor)
        
        page = await self.page_dao.get_by_app_and_page_uuid(app.version_id, page_uuid)
        if not page: raise NotFoundError("Page not found")

        data = update_data.model_dump(exclude_unset=True)
        for k, v in data.items():
            setattr(page, k, v)
        
        await self.db.flush()
        
        # 如果更新了 DSL (data)，触发全量引用同步
        if "data" in data:
            await self._sync_all_dependencies(app)
            
        return UiPageDetail.model_validate(page)

    async def delete_page(self, app_uuid: str, page_uuid: str, actor: User):
        """删除页面"""
        app = await self._get_workspace_app_for_edit(app_uuid, actor)
        
        page = await self.page_dao.get_by_app_and_page_uuid(app.version_id, page_uuid)
        if not page: raise NotFoundError("Page not found")
        
        # 检查是否是主页
        if app.home_page_id == page.page_uuid:
            raise ServiceException("Cannot delete the Home Page. Set another home page first.")

        await self.db.delete(page)
        await self.db.flush()
        
        # 触发引用同步 (移除该页面产生的引用)
        await self._sync_all_dependencies(app)

    async def _get_workspace_app_for_edit(self, app_uuid: str, actor: User) -> UiApp:
        """Helper: 获取并校验 App 处于可编辑状态"""
        app = await self.get_by_uuid(app_uuid)
        if not app: raise NotFoundError("App not found")
        if app.status != VersionStatus.WORKSPACE:
            raise ServiceException("Only workspace instances can be edited.")
        # 鉴权
        await self.context.perm_evaluator.ensure_can(["resource:update"], target=app.resource.workspace)
        return app

    # ==========================================================================
    # 2. App Level Operations (Impl Service)
    # ==========================================================================

    async def get_by_uuid(self, instance_uuid: str) -> Optional[UiApp]:
        return await self.dao.get_by_uuid(instance_uuid)

    async def serialize_instance(self, instance: UiApp) -> Dict[str, Any]:
        """
        [Metadata View] 默认只返回骨架，用于列表或首屏。
        前端编辑器初始化时，会先获取这个，然后按需请求 `get_page_detail`。
        """
        return UiAppMetadataRead.model_validate(instance).model_dump()

    async def create_instance(self, resource: Resource, actor: User) -> UiApp:
        instance = UiApp(
            version_tag="__workspace__",
            status=VersionStatus.WORKSPACE,
            creator_id=actor.id,
            resource_type="uiapp",
            name=resource.name,
            resource=resource,
            home_page_uuid="page_home",
            global_config={"theme": "default"},
            navigation={}
        )
        self.db.add(instance)
        await self.db.flush()

        # 创建默认主页
        home_page = UiPage(
            app_version_id=instance.version_id,
            page_uuid="page_home",
            path="/",
            label="Home",
            display_order=0,
            data=[] # 空白 DSL
        )
        self.db.add(home_page)
        return instance

    async def update_instance(self, instance: UiApp, update_data: Dict[str, Any]) -> UiApp:
        """
        App 级更新 (全局配置, 导航, 主页设置)。
        不处理 pages 的增删改 (由 create_page/update_page 处理)。
        """
        if instance.status != VersionStatus.WORKSPACE:
            raise ServiceException("Only workspace instances can be updated.")

        try:
            validated = UiAppUpdate.model_validate(update_data)
        except ValidationError as e:
            raise ServiceException(f"Invalid Update Data: {e}")

        data_dict = validated.model_dump(exclude_unset=True)
        for k, v in data_dict.items():
            setattr(instance, k, v)
        
        return instance

    async def _sync_all_dependencies(self, app: UiApp):
        """
        [Atomic Sync Strategy]
        扫描所有页面，计算全局引用集合，并与数据库进行原子同步。
        """
        # 1. 内存计算：提取最新的全量引用
        pages = await self.page_dao.get_list(where={"app_version_id": app.version_id})
        
        calculated_refs_map = {} 
        extraction_errors = [] # 收集错误

        for page in pages:
            try:
                # [关键] 这里是反序列化校验点
                # 如果前端发来的 JSON 不符合 UiNode 的骨架定义，这里会抛出 ValidationError
                nodes = [UiNode.model_validate(n) for n in page.data]
                
                # 提取依赖
                page_refs = self.extractor.extract_from_nodes(nodes)
                
                for ref in page_refs:
                    key = (ref.target_instance_uuid, ref.source_node_uuid)
                    if key not in calculated_refs_map:
                        calculated_refs_map[key] = ref

            except Exception as e:
                # 记录详细错误，但不立即中断循环，以便收集所有页面的问题
                error_msg = f"Page '{page.label}' (UUID: {page.page_uuid}) DSL parsing failed: {str(e)}"
                logger.error(error_msg)
                extraction_errors.append(error_msg)

        # 如果存在解析错误，绝对禁止继续！
        # 否则会导致这一页原本存在的依赖被视为“用户删除了”，从而导致数据库误删。
        if extraction_errors:
            # 抛出 ServiceException，FastAPI 会将其转换为 400 Bad Request 返回给前端
            # 并且由于此方法通常在 update_page 的事务或流程中，它会阻止提交。
            raise ServiceException(
                f"Dependency extraction failed. Saving aborted to prevent data corruption. Errors: {'; '.join(extraction_errors)}"
            )

        # 2. 数据库查询：获取当前存储的引用
        existing_refs_list = await self.ref_service.dao.get_dependencies(app.id)
        existing_refs_map = {
            (r.target_instance.uuid, r.source_node_uuid): r 
            for r in existing_refs_list if r.target_instance
        }

        # 3. 计算 Diff
        new_keys = set(calculated_refs_map.keys())
        old_keys = set(existing_refs_map.keys())
        
        keys_to_add = new_keys - old_keys
        keys_to_remove = old_keys - new_keys
        
        if not keys_to_add and not keys_to_remove:
            return # 无变更，提前结束

        # 4. 执行原子更新 (Critical Transaction)
        # 使用 begin_nested() 确保这一系列 DB 操作要么全成功，要么全失败
        async with self.db.begin_nested():
            # A. 移除废弃引用
            for key in keys_to_remove:
                ref_to_del = existing_refs_map[key]
                await self.db.delete(ref_to_del)
            
            # B. 添加新引用
            for key in keys_to_add:
                ref_data = calculated_refs_map[key]
                # 调用 RefService 添加，确保鉴权和校验逻辑复用
                # 注意：这里可能抛出 NotFoundError 或 PermissionDeniedError
                # 如果抛出，整个事务回滚，Save 操作失败。这是正确的行为：不能保存无效的引用。
                await self.ref_service.add_dependency(
                    source_instance_uuid=app.uuid,
                    ref_data=ref_data,
                    actor=self.context.actor
                )

    async def delete_instance(self, instance: UiApp) -> None:
        # UiPage 会通过 Cascade 自动删除
        await self.db.delete(instance)

    async def on_resource_delete(self, resource: Resource) -> None:
        pass

    async def publish_instance(self, workspace_instance: UiApp, version_tag: str, version_notes: Optional[str], actor: User) -> UiApp:
        """
        [Publish] 深拷贝 App 及其所有 Page。
        """
        # 1. 校验
        validation = await self.validate_instance(workspace_instance)
        if not validation.is_valid:
            raise ServiceException(f"Cannot publish: {'; '.join(validation.errors)}")

        async with self.db.begin_nested():
            # 2. 拷贝 App 骨架
            snapshot_app = UiApp(
                resource_id=workspace_instance.resource_id,
                status=VersionStatus.PUBLISHED,
                version_tag=version_tag,
                version_notes=version_notes,
                creator_id=actor.id,
                name=workspace_instance.name,
                description=workspace_instance.description,
                global_config=workspace_instance.global_config.copy(),
                navigation=workspace_instance.navigation.copy() if workspace_instance.navigation else None,
                home_page_uuid=workspace_instance.home_page_uuid
            )
            self.db.add(snapshot_app)
            await self.db.flush() # 获取 snapshot_app.version_id

            # 3. 拷贝所有 Page
            # 使用 SQL 级批量插入最快，但为了简单和兼容 JSON 字段，这里用 ORM 循环
            source_pages = await self.page_dao.get_list(where={"app_version_id": workspace_instance.version_id})
            
            for src_page in source_pages:
                new_page = UiPage(
                    app_version_id=snapshot_app.version_id,
                    page_uuid=src_page.page_uuid,
                    path=src_page.path,
                    label=src_page.label,
                    icon=src_page.icon,
                    display_order=src_page.display_order,
                    data=src_page.data, # JSON 字段，SQLAlchemy 会处理
                    config=src_page.config
                )
                self.db.add(new_page)
        
        return snapshot_app

    # ==========================================================================
    # 3. Validation & Utils
    # ==========================================================================

    async def validate_instance(self, instance: UiApp) -> ValidationResult:
        errors = []
        
        # 检查是否有主页
        if not instance.home_page_uuid:
            errors.append("应用未设置主页")
        else:
            # 确认主页存在
            home = await self.page_dao.get_by_app_and_page_uuid(instance.version_id, instance.home_page_uuid)
            if not home:
                errors.append(f"指定的主页 (UUID: {instance.home_page_uuid}) 不存在")

        # 检查是否有页面
        count = await self.page_dao.count(where={"app_version_id": instance.version_id})
        if count == 0:
            errors.append("应用至少需要一个页面")

        return ValidationResult(is_valid=not errors, errors=errors)

    async def get_dependencies(self, instance: UiApp) -> List[DependencyInfo]:
        # 上游 ResourceService 已完成该实例的鉴权，这里只做依赖查询本身，避免重复查询与重复鉴权。
        refs: List[ResourceRef] = await self.ref_service.dao.get_dependencies(instance.id)
        return [
            DependencyInfo(
                resource_uuid=r.target_resource.uuid,
                instance_uuid=r.target_instance.uuid,
                alias=r.alias
            ) for r in refs
        ]

    async def get_searchable_content(self, instance: UiApp) -> str:
        # 仅索引 App 名称和描述，以及页面标题
        # 不加载 Page DSL 以节省性能
        base_text = f"{instance.name} {instance.description or ''}"
        pages = await self.page_dao.get_list(where={"app_version_id": instance.version_id})
        page_texts = [p.label for p in pages]
        return base_text + " " + " ".join(page_texts)

    async def as_llm_tool(self, instance: UiApp) -> Optional[LLMTool]:
        return None

    async def execute(self, *args, **kwargs):
        raise ServiceException("UiApp cannot be executed on backend.")

    async def execute_batch(self, *args, **kwargs):
        raise ServiceException("UiApp cannot be executed on backend.")
