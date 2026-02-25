# src/app/services/resource/resource_type_service.py

from typing import List
from app.core.context import AppContext
from app.models.resource import ResourceType
from app.schemas.resource.resource_type_schemas import ResourceTypeCreate, ResourceTypeUpdate, ResourceTypeRead
from app.system.resource.resource_type_manager import ResourceTypeManager
from app.services.base_service import BaseService

class ResourceTypeService(BaseService):
    """
    服务层：负责 ResourceType 相关的业务流程编排和权限检查。
    这是API层与系统核心逻辑交互的唯一入口。
    """
    def __init__(self, context: AppContext):
        self.context = context
        # 服务层使用 Manager 来执行核心操作
        self.manager = ResourceTypeManager(context.db)

    async def create_resource_type(self, type_data: ResourceTypeCreate) -> ResourceTypeRead:
        """
        创建一个新的资源类型，包含权限检查。
        """
        # 1. 权限检查
        await self.context.perm_evaluator.ensure_can(["platform:resourcetype:manage"])
        
        # 2. 调用核心逻辑
        new_type = await self.manager.create_resource_type(type_data)
        return ResourceTypeRead.model_validate(new_type)

    async def get_all_resource_types(self) -> List[ResourceTypeRead]:
        """
        获取所有资源类型。此操作是公开的，不需要权限检查。
        """
        types = await self.manager.get_all_resource_types()
        return [ResourceTypeRead.model_validate(t) for t in types]

    async def update_resource_type(self, name: str, update_data: ResourceTypeUpdate) -> ResourceType:
        """
        更新一个资源类型，包含权限检查。
        """
        # 1. 权限检查
        await self.context.perm_evaluator.ensure_can(["platform:resourcetype:manage"])

        # 2. 调用核心逻辑
        updated_type = await self.manager.update_resource_type(name, update_data)
        return ResourceTypeRead.model_validate(updated_type)

    async def delete_resource_type(self, name: str) -> None:
        """
        删除一个资源类型，包含权限检查。
        """
        # 1. 权限检查
        await self.context.perm_evaluator.ensure_can(["platform:resourcetype:manage"])

        # 2. 调用核心逻辑
        await self.manager.delete_resource_type(name)