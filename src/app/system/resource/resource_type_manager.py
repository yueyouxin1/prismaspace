# src/app/system/resource/resource_type_manager.py

from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from app.models.resource import ResourceType
from app.dao.resource.resource_type_dao import ResourceTypeDao
from app.schemas.resource.resource_type_schemas import ResourceTypeCreate, ResourceTypeUpdate
from app.services.exceptions import ServiceException, NotFoundError

class ResourceTypeManager:
    """
    系统级管理器，负责 ResourceType 的核心业务逻辑。
    它不处理权限，只专注于数据操作的正确性。
    """
    def __init__(self, db: AsyncSession):
        self.db = db
        self.dao = ResourceTypeDao(db)

    async def create_resource_type(self, type_data: ResourceTypeCreate) -> ResourceType:
        """
        创建一个新的资源类型。
        :raises ServiceException: 如果同名类型已存在。
        """
        existing_type = await self.dao.get_one(where={"name": type_data.name})
        if existing_type:
            raise ServiceException(f"Resource type with name '{type_data.name}' already exists.")

        new_type = ResourceType(**type_data.model_dump())
        return await self.dao.add(new_type)

    async def get_all_resource_types(self) -> List[ResourceType]:
        """获取所有已定义的资源类型。"""
        return await self.dao.get_list()

    async def get_resource_type_by_name(self, name: str) -> ResourceType:
        """按名称获取单个资源类型。"""
        resource_type = await self.dao.get_one(where={"name": name})
        if not resource_type:
            raise NotFoundError(f"Resource type with name '{name}' not found.")
        return resource_type

    async def update_resource_type(self, name: str, update_data: ResourceTypeUpdate) -> ResourceType:
        """更新一个已存在的资源类型。"""
        resource_type = await self.get_resource_type_by_name(name)
        
        update_dict = update_data.model_dump(exclude_unset=True)
        if not update_dict:
            return resource_type # 没有需要更新的字段

        for key, value in update_dict.items():
            setattr(resource_type, key, value)
        
        await self.db.flush()
        await self.db.refresh(resource_type)
        return resource_type

    async def delete_resource_type(self, name: str) -> None:
        """删除一个资源类型（高危操作）。"""
        resource_type = await self.get_resource_type_by_name(name)
        # 注意: 数据库层面的外键约束会防止删除仍被引用的ResourceType。
        # 服务层可以添加额外的检查，例如检查是否有Resource正在使用此类型。
        await self.db.delete(resource_type)
        await self.db.flush()