# app/services/resource/resource_service.py

from typing import Dict, List, Any
from app.core.context import AppContext
from app.models import User
from app.models.resource import Resource, ResourceInstance, ResourceRef, VersionStatus
from app.dao.resource.resource_dao import ResourceDao
from app.dao.workspace.workspace_dao import WorkspaceDao
from app.dao.resource.resource_ref_dao import ResourceRefDao
from app.dao.resource.resource_type_dao import ResourceTypeDao
from app.schemas.resource.resource_schemas import (
    ResourceCreate,
    ResourceUpdate,
    ResourceRead,
    ResourceDetailRead,
    InstancePublish,
    ResourceDependencyRead,
)
from .base.base_resource_service import BaseResourceService
from app.services.exceptions import NotFoundError, ServiceException

class ResourceService(BaseResourceService):
    def __init__(self, context: AppContext):
        super().__init__(context)
        self.dao = ResourceDao(context.db)
        self.workspace_dao = WorkspaceDao(context.db)
        self.resource_type_dao = ResourceTypeDao(context.db)
        self.ref_dao = ResourceRefDao(context.db)

    async def serialize_instance(self, instance: ResourceInstance) -> Dict[str, Any]:
        """[终极版] 纯粹的分发器。"""
        impl_service = await self._get_impl_service_by_instance(instance)
        # 实现服务自己负责序列化
        return await impl_service.serialize_instance(instance)

    # --- Public DTO-returning "Wrapper" Method ---
    async def get_resources_in_workspace(self, workspace_uuid: str, actor: User) -> list[ResourceRead]:
        resources = await self._get_resources_in_workspace(workspace_uuid, actor)
        return [ResourceRead.model_validate(r) for r in resources]

    async def get_resource_details_by_uuid(self, resource_uuid: str, actor: User) -> ResourceDetailRead:
        """
        使用一次高效查询获取资源详情，主要用于填充编辑器。
        """
        # 1. [关键] 使用一次数据库查询，通过 withs (或 options) 预加载所有需要的数据。
        #    这里我们需要 Resource, 它的 workspace (用于鉴权),
        #    以及它的 workspace_instance (用于编辑)。
        resource = await self.dao.get_resource_details_by_uuid(resource_uuid)
        if not resource:
            raise NotFoundError("Resource not found.")
            
        await self.context.perm_evaluator.ensure_can(["resource:read"], target=resource.workspace)

        # 2. 手动构建聚合响应字典，确保结构清晰
        # 首先，序列化 Resource 本身
        details = ResourceRead.model_validate(resource).model_dump()
        
        # 3. 序列化并嵌入 workspace_instance 的完整内容
        details['workspace_instance'] = None
        if resource.workspace_instance:
            # 获取专家服务
            impl_service = await self._get_impl_service_by_type(resource.resource_type.name)
            
            # 让专家服务加载完整的、包含深层关系的对象
            full_workspace_instance = await impl_service.get_by_uuid(resource.workspace_instance.uuid)
            
            if full_workspace_instance:
                # 让专家服务用这个完整的对象进行序列化
                details['workspace_instance'] = await impl_service.serialize_instance(full_workspace_instance)
        
        # 4. 只提供 latest_published_instance 的 UUID
        details['latest_published_instance_uuid'] = resource.latest_published_instance.uuid if resource.latest_published_instance else None
                
        return details

    async def get_resource_instances_by_uuid(self, resource_uuid: str, actor: User) -> List[Dict[str, Any]]:
        return await self._get_resource_instances_by_uuid(resource_uuid, actor)
        
    async def create_resource_in_workspace(self, workspace_uuid: str, resource_data: ResourceCreate, actor: User) -> ResourceRead:
        new_resource = await self._create_resource_in_workspace(workspace_uuid, resource_data, actor)
        return ResourceRead.model_validate(new_resource)

    async def update_resource_metadata(self, resource_uuid: str, update_data: ResourceUpdate, actor: User) -> ResourceRead:
        updated_resource = await self._update_resource_metadata(resource_uuid, update_data, actor)
        return ResourceRead.model_validate(updated_resource)

    async def delete_resource(self, resource_uuid: str, actor: User) -> None:
        await self._delete_resource(resource_uuid, actor)
        
    async def get_instance_by_uuid(self, instance_uuid: str, actor: User) -> Dict[str, Any]:
        full_instance = await self._get_instance_by_uuid(instance_uuid, actor)
        return await self.serialize_instance(full_instance)

    async def update_instance_by_uuid(self, instance_uuid: str, update_data: Dict[str, Any], actor: User) -> Dict[str, Any]:
        updated_instance = await self._update_instance_by_uuid(instance_uuid, update_data, actor)
        return await self.serialize_instance(updated_instance)

    async def get_instance_dependencies(self, instance_uuid: str, actor: User) -> list[ResourceDependencyRead]:
        dependencies = await self._get_instance_dependencies(instance_uuid, actor)
        return [ResourceDependencyRead.model_validate(dep) for dep in dependencies]

    async def delete_instance_by_uuid(self, instance_uuid: str, actor: User) -> None:
        await self._delete_instance_by_uuid(instance_uuid, actor)

    async def publish_instance(
        self, 
        instance_uuid: str, 
        publish_data: InstancePublish, 
        actor: User
    ) -> Dict[str, Any]:
        new_published_instance = await self._publish_instance(instance_uuid, publish_data, actor)
        return await self.serialize_instance(new_published_instance)

    async def archive_instance(
        self, 
        instance_uuid: str, 
        actor: User
    ) -> Dict[str, Any]:
        archived_instance = await self._archive_instance(instance_uuid, actor)
        return await self.serialize_instance(archived_instance)

    # --- Internal ORM-returning "Workhorse" Method ---
    async def _get_resources_in_workspace(self, workspace_uuid: str, actor: User) -> list[Resource]:
        workspace = await self.workspace_dao.get_by_uuid(workspace_uuid, withs=["user_owner", "team"])
        if not workspace:
            raise NotFoundError("Workspace not found.")

        await self.context.perm_evaluator.ensure_can(["resource:read"], target=workspace)
        resources = await self.dao.get_resources_by_workspace_id(workspace.id)
        return resources

    async def _create_resource_in_workspace(self, workspace_uuid: str, resource_data: ResourceCreate, actor: User) -> Resource:
        # 1. 通用逻辑 (权限、验证) - 保持不变
        workspace = await self.workspace_dao.get_by_uuid(workspace_uuid, withs=["user_owner", "team"])
        if not workspace:
            raise NotFoundError("Workspace not found.")
        await self.context.perm_evaluator.ensure_can(["resource:create"], target=workspace)

        resource_type = await self.resource_type_dao.get_one(where={"name": resource_data.resource_type})
        if not resource_type:
            raise ServiceException(f"Resource type '{resource_data.resource_type}' is not supported.")

        # 2. 创建通用的 Resource "身份卡"
        new_resource = Resource(
            name=resource_data.name,
            description=resource_data.description,
            workspace_id=workspace.id,
            resource_type_id=resource_type.id,
            creator_id=actor.id
        )

        self.db.add(new_resource)
        await self.db.flush()

        # 3. [核心重构] 将实例化的职责分派给专门的服务
        impl_service = await self._get_impl_service_by_type(resource_type.name)
        new_instance = await impl_service.create_instance(resource=new_resource, actor=actor)

        # 统一收敛：工作区实例必须与 Resource 元数据一致；实例类型必须与注册服务名一致。
        new_instance.name = new_resource.name
        new_instance.description = new_resource.description
        new_instance.resource_type = impl_service.name
        
        # 4. 建立通用关系并持久化
        new_resource.workspace_instance = new_instance
        if new_instance.status == VersionStatus.PUBLISHED:
            # 某些资源实例在创建时就是发布版本
            new_resource.latest_published_instance = new_instance

        await self.db.flush()

        # 5. 序列化需要instance.uuid，re-fetch比refresh更安全
        final_resource = await self.dao.get_one(
            where={"id": new_resource.id},
            withs=["creator", "resource_type", "workspace_instance", "latest_published_instance"]
        )
        
        if not final_resource:
            # 这几乎不可能发生，但作为防御性编程
            raise ServiceException("Failed to retrieve newly created resource.")
            
        return final_resource

    async def _update_resource_metadata(self, resource_uuid: str, update_data: ResourceUpdate, actor: User) -> Resource:
        """
        更新 Resource 元数据，并同步更新活跃的 workspace_instance。
        """
        # 1. 获取 Resource 并预加载 workspace_instance 以便更新
        resource = await self.dao.get_by_uuid(
            resource_uuid,
            # 加载基础实例数据
            withs=["workspace", "workspace_instance"]
        )
        if not resource:
            raise NotFoundError("Resource not found.")
        
        await self.context.perm_evaluator.ensure_can(["resource:update"], target=resource.workspace)

        # 2. 更新 Resource 实体 (单一事实来源)
        update_dict = update_data.model_dump(exclude_unset=True)
        for key, value in update_dict.items():
            setattr(resource, key, value)
        
        resource.last_modifier_id = actor.id

        # 3. [关键] 同步更新活跃的 workspace_instance (工作副本)
        if resource.workspace_instance:
            # 仅同步基类元字段，避免触发多态实例的懒加载属性访问
            for key, value in update_dict.items():
                if key in {"name", "description"}:
                    setattr(resource.workspace_instance, key, value)

        # 4. 持久化所有更改
        await self.db.flush()

        # 5. 重新查询
        final_resource = await self.dao.get_one(
            where={"id": resource.id},
            withs=["creator", "resource_type", "workspace_instance", "latest_published_instance"]
        )
        
        if not final_resource:
            # 这几乎不可能发生，但作为防御性编程
            raise ServiceException("Failed to retrieve newly created resource.")
            
        return final_resource

    async def _delete_resource(self, resource_uuid: str, actor: User) -> None:
        """
        [REFACTORED - CRITICAL] 永久删除一个 Resource 及其所有关联的 Instances 和物理资源。
        这是一个服务层级的级联删除操作。
        """
        # 1. 获取 Resource，并预加载所有需要被删除的子实例      
        resource = await self.dao.get_by_uuid(
            resource_uuid,
            # 仅加载 workspace 和 workspace_instance 指针字段，完整实例后续按类型加载。
            withs=["workspace", "resource_type", "workspace_instance"]
        )

        if not resource:
            raise NotFoundError("Resource not found.")
            
        # 2. 权限检查
        await self.context.perm_evaluator.ensure_can(["resource:delete"], target=resource.workspace)

        # 3. [服务层级联] 遍历所有子实例，并委托给专家服务进行删除

        if not resource.workspace_instance:
             raise RuntimeError(f"Data Integrity Error: Resource {resource.uuid} has no workspace_instance. Cannot perform clean deletion.")

        # 获取对应的专家服务
        impl_service = await self._get_impl_service_by_type(resource.resource_type.name)

        await impl_service.on_resource_delete(resource)
        
        await self.db.delete(resource)
        
        # 5. 提交所有数据库变更
        await self.db.flush()

    async def _get_instance_by_uuid(self, instance_uuid: str, actor: User) -> ResourceInstance:
        instance, _ = await self._get_full_instance_and_service(instance_uuid)
        await self.context.perm_evaluator.ensure_can(["resource:read"], target=instance.resource.workspace)
        return instance

    async def _get_resource_instances_by_uuid(self, resource_uuid: str, actor: User) -> List[Dict[str, Any]]:
        resource = await self.dao.get_by_uuid(resource_uuid, withs=["workspace"])
        if not resource:
            raise NotFoundError("Resource not found.")

        await self.context.perm_evaluator.ensure_can(["resource:read"], target=resource.workspace)

        # 版本列表只返回摘要字段，避免“每版本一次 full instance 查询”的串行 N+1。
        return await self.instance_dao.list_version_summaries_by_resource_id(resource.id)

    async def _get_instance_dependencies(self, instance_uuid: str, actor: User):
        instance_stub = await self._get_instance_stub_by_uuid(instance_uuid)
        await self.context.perm_evaluator.ensure_can(["resource:read"], target=instance_stub.resource.workspace)
        service = await self._get_impl_service_by_type(instance_stub.resource_type)
        return await service.get_dependencies(instance_stub)

    async def _publish_instance(
        self, 
        instance_uuid: str, 
        publish_data: InstancePublish, 
        actor: User
    ) -> ResourceInstance:
        """
        [核心业务逻辑] 发布一个工作区实例。
        这会原子地将旧的发布版本归档，并创建一个新的 PUBLISHED 快照。
        """
        # 1. 直接按类型加载完整实例（发布快照需要子类字段）
        source_instance, impl_service = await self._get_full_instance_and_service(instance_uuid)
        if source_instance.status != VersionStatus.WORKSPACE:
            raise ServiceException("Only a workspace instance can be published.")

        # 2. 权限检查
        resource = source_instance.resource
        await self.context.perm_evaluator.ensure_can(["resource:publish"], target=resource.workspace)

        # 发布前做一次一致性自愈：workspace 实例元数据以 Resource 为准。
        source_instance.name = resource.name
        source_instance.description = resource.description

        # 3. 验证版本标签的唯一性
        existing_version = await self.instance_dao.get_one(where={
            "resource_id": resource.id,
            "version_tag": publish_data.version_tag
        })
        if existing_version:
            raise ServiceException(f"Version tag '{publish_data.version_tag}' already exists for this resource.")

        # 4. 获取专家服务来创建新版本的“快照”
        async with self.db.begin_nested():
            # 1. 创建快照 (委托给 impl_service)
            new_published_instance = await impl_service.publish_instance(
                workspace_instance=source_instance,
                version_tag=publish_data.version_tag,
                version_notes=publish_data.version_notes,
                actor=actor
            )

            self.db.add(new_published_instance)
            await self.db.flush()

            # 2. 复制依赖关系
            # 将 source_instance 的所有 ResourceRef 复制一份给 new_published_instance
            # 因为 source 指向的是 PUBLISHED，所以这些引用在生产环境是绝对安全的
            existing_refs = await self.ref_dao.get_dependencies(source_instance.id)
            
            new_refs = []
            for ref in existing_refs:
                new_ref = ResourceRef(
                    source_resource_id=new_published_instance.resource_id,
                    source_instance_id=new_published_instance.id, # 指向新发布的版本
                    target_resource_id=ref.target_resource_id,
                    target_instance_id=ref.target_instance_id,    # 指向同一个稳定的目标
                    source_node_uuid=ref.source_node_uuid,
                    alias=ref.alias,
                    options=ref.options
                )
                new_refs.append(new_ref)
            
            if new_refs:
                self.db.add_all(new_refs)

            # 3. 更新 Resource 的指针并持久化
            resource.latest_published_instance_id = new_published_instance.id
            await self.db.flush()

        full_published_instance = await impl_service.get_by_uuid(new_published_instance.uuid)
        if not full_published_instance:
            raise NotFoundError("Published instance not found after persist.")
        return full_published_instance

    async def _archive_instance(self, instance_uuid: str, actor: User) -> ResourceInstance:
        """手动归档一个已发布的版本。"""
        instance, service = await self._get_full_instance_and_service(instance_uuid)
        if instance.status != VersionStatus.PUBLISHED:
            raise ServiceException("Only a published instance can be archived.")

        await self.context.perm_evaluator.ensure_can(["resource:publish"], target=instance.resource.workspace)
        
        instance.status = VersionStatus.ARCHIVED

        # [关键] 检查这个被归档的版本是否是 "最新" 版本
        if instance.resource.latest_published_instance_id == instance.id:
            # 如果是，我们需要将 "最新" 指针回退到上一个可用的发布版本
            previous_published = await self.instance_dao.get_one(
                where=[
                    ResourceInstance.resource_id == instance.resource_id,
                    ResourceInstance.status == VersionStatus.PUBLISHED,
                    ResourceInstance.id != instance.id # 排除当前正在被归档的这个
                ],
                order=[self.instance_dao.model.created_at.desc()]
            )
            instance.resource.latest_published_instance_id = previous_published.id if previous_published else None
            
        await self.db.flush()
        full_instance = await service.get_by_uuid(instance_uuid)
        if not full_instance:
            raise NotFoundError("Resource instance not found.")
        return full_instance

    async def _update_instance_by_uuid(self, instance_uuid: str, update_data: Dict[str, Any], actor: User) -> ResourceInstance:
        instance, service = await self._get_full_instance_and_service(instance_uuid)
        await self.context.perm_evaluator.ensure_can(["resource:update"], target=instance.resource.workspace)
        previous_name = instance.name
        previous_description = instance.description
        updated_instance = await service.update_instance(instance, update_data)

        # 统一收敛：若编辑的是当前工作区实例，实例与 Resource 必须强一致。
        if (
            updated_instance.status == VersionStatus.WORKSPACE
            and updated_instance.resource.workspace_instance_id == updated_instance.id
        ):
            if previous_name != updated_instance.name:
                updated_instance.resource.name = updated_instance.name
            if previous_description != updated_instance.description:
                updated_instance.resource.description = updated_instance.description

        await self.db.flush()
        full_instance = await service.get_by_uuid(updated_instance.uuid)
        if not full_instance:
            raise NotFoundError("Resource instance not found.")
        return full_instance

    async def _delete_instance_by_uuid(self, instance_uuid: str, actor: User) -> None:
        """
        永久删除一个特定的 ResourceInstance (版本)。
        """
        # 1. 获取完整实例（用于权限与类型化删除逻辑）
        instance, service = await self._get_full_instance_and_service(instance_uuid)
        
        # 2. 执行通用逻辑：权限检查
        # 注意：删除版本也应该使用 "resource:delete" 权限，因为它同样具有破坏性
        await self.context.perm_evaluator.ensure_can(["resource:delete"], target=instance.resource.workspace)
        
        # 3. [关键业务规则] 添加保护性检查
        resource = instance.resource
        if resource.workspace_instance_id == instance.id:
            raise ServiceException("Cannot delete the active workspace instance. Please switch to another version to edit first.")
        
        # 4. 委托给专家执行删除
        await service.delete_instance(instance)
        await self.db.flush()
