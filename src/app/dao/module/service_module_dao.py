# src/app/dao/module/service_module_dao.py

from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.dao.base_dao import BaseDao
from app.models.module import ServiceModule, ServiceModuleVersion, ServiceModuleType, ServiceModuleProvider

class ServiceModuleTypeDao(BaseDao[ServiceModuleType]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(model_class=ServiceModuleType, db_session=db_session)

class ServiceModuleProviderDao(BaseDao[ServiceModuleProvider]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(model_class=ServiceModuleProvider, db_session=db_session)

    async def get_by_name(self, name: str, withs: Optional[list] = None) -> Optional[ServiceModuleProvider]:
        return await self.get_one(where={"name": name}, withs=withs)

class ServiceModuleDao(BaseDao[ServiceModule]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(model_class=ServiceModule, db_session=db_session)

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[ServiceModule]:
        return await self.get_one(where={"uuid": uuid}, withs=withs)

class ServiceModuleVersionDao(BaseDao[ServiceModuleVersion]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(model_class=ServiceModuleVersion, db_session=db_session)

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[ServiceModuleVersion]:
        return await self.get_one(where={"uuid": uuid}, withs=withs)

    async def get_default_version_by_type(self, type_name: str) -> Optional[ServiceModuleVersion]:
        """
        根据模块类型名称（如 'llm', 'embedding'）获取系统配置的默认版本。
        """
        stmt = (
            select(ServiceModuleVersion)
            .join(ServiceModuleType, ServiceModuleType.default_version_id == ServiceModuleVersion.id)
            .where(ServiceModuleType.name == type_name)
            # 预加载必要的属性，防止后续访问出错
            .options(
                joinedload(ServiceModuleVersion.service_module),
                joinedload(ServiceModuleVersion.features) # 通常运行时需要 feature 信息
            )
        )
        result = await self.db_session.execute(stmt)
        return result.scalars().first()