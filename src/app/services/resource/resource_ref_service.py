# app/services/resource/resource_ref_service.py

from typing import List
from app.core.context import AppContext
from app.models import User
from app.models.resource import Resource, ResourceInstance, ResourceType, ResourceRef, VersionStatus
from app.dao.resource.resource_ref_dao import ResourceRefDao
from app.schemas.resource.resource_ref_schemas import ReferenceCreate, ReferenceRead, BatchSyncReferences
from .base.base_resource_service import BaseResourceService
from app.services.exceptions import NotFoundError, ServiceException, PermissionDeniedError

class ResourceRefService(BaseResourceService):
    def __init__(self, context: AppContext):
        super().__init__(context)
        self.dao = ResourceRefDao(context.db)

    async def add_dependency(self, source_instance_uuid: str, ref_data: ReferenceCreate, actor: User) -> ReferenceRead:
        new_ref = await self._add_dependency(source_instance_uuid, ref_data, actor)
        return ReferenceRead.model_validate(new_ref)

    async def remove_dependency(self, source_instance_uuid: str, target_instance_uuid: str, source_node_uuid: str = None, actor: User = None) -> None:
        return await self._remove_dependency(source_instance_uuid, target_instance_uuid, source_node_uuid, actor)

    async def list_dependencies(self, instance_uuid: str, actor: User) -> List[ReferenceRead]:
        refs = await self._list_dependencies(instance_uuid, actor)
        return [ReferenceRead.model_validate(r) for r in refs]

    # =================================================================
    # Reference Management (Dependency Sub-system)
    # =================================================================

    async def _add_dependency(self, source_instance_uuid: str, ref_data: ReferenceCreate, actor: User) -> ResourceRef:
        """
        为源实例添加一个依赖引用。
        """
        # 1. 验证源实例 (Source)
        source_instance = await self._get_instance_stub_by_uuid(source_instance_uuid)
        
        # 鉴权：必须有权修改源资源
        await self.context.perm_evaluator.ensure_can(["resource:update"], target=source_instance.resource.workspace)

        # 验证源实例状态：只能给草稿版本添加依赖
        if source_instance.status != VersionStatus.WORKSPACE:
            raise ServiceException("Dependencies can only be added to Workspace instances.")

        # 2. 验证目标实例 (Target)
        target_instance = await self._get_instance_stub_by_uuid(ref_data.target_instance_uuid)
        
        # 仅允许引用发布版本（因为发布版本通常经过用户调试验证可用）
        if target_instance.status != VersionStatus.PUBLISHED:
            raise ServiceException("Target dependencies can only be added to Published instances.")

        # 鉴权：必须有权读取目标资源（可见性检查）
        # 逻辑：目标是公开的 OR 用户有权读取目标所在的Workspace
        target_workspace = target_instance.resource.workspace
        has_read_perm = await self.context.perm_evaluator.can(["resource:read"], target=target_workspace)
        
        if target_instance.visibility != 'public' and not has_read_perm:
             raise PermissionDeniedError(f"Cannot reference target resource {target_instance.uuid}: Access denied.")

        # 3. 循环依赖检查 (不能引用自己)
        if source_instance.id == target_instance.id:
            raise ServiceException("Cannot reference self.")

        # 4. 创建引用
        # 检查是否已存在（避免重复）
        # 假设同源同节点同目标只能有一条
        # ... (Get existing logic omitted for brevity, usually rely on DB constraint or check) ...

        new_ref = ResourceRef(
            source_resource_id=source_instance.resource_id, # 冗余字段自动填充
            source_instance_id=source_instance.id,
            target_resource_id=target_instance.resource_id, # 冗余字段自动填充
            target_instance_id=target_instance.id,
            source_node_uuid=ref_data.source_node_uuid,
            alias=ref_data.alias,
            options=ref_data.options
        )
        self.db.add(new_ref)
        await self.db.flush()
        
        # 重新加载以返回完整信息（包含关联对象）
        await self.db.refresh(new_ref)
        # 手动填充关联对象以便 Schema 读取 (或者在 DAO 中 get_by_pk)
        new_ref.target_instance = target_instance
        new_ref.target_resource = target_instance.resource
        
        return new_ref

    async def _remove_dependency(self, source_instance_uuid: str, target_instance_uuid: str, source_node_uuid: str = None, actor: User = None) -> None:
        """
        移除依赖。
        """
        source_instance = await self._get_instance_stub_by_uuid(source_instance_uuid)
        
        await self.context.perm_evaluator.ensure_can(["resource:update"], target=source_instance.resource.workspace)
        
        target_instance = await self._get_instance_stub_by_uuid(target_instance_uuid)

        await self.dao.delete_by_source_and_target(
            source_instance.id, 
            target_instance.id,
            source_node_uuid
        )
        await self.db.flush()

    async def _list_dependencies(self, instance_uuid: str, actor: User) -> List[ResourceRef]:
        """
        列出某实例的所有依赖。
        """
        instance = await self._get_instance_stub_by_uuid(instance_uuid)
        
        await self.context.perm_evaluator.ensure_can(["resource:read"], target=instance.resource.workspace)
        
        refs = await self.dao.get_dependencies(instance.id)
        return refs
