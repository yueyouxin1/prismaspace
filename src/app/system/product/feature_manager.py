# src/app/system/product/feature_manager.py

from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from app.models import Feature, ServiceModuleVersion
from app.dao.product.feature_dao import FeatureDao
from app.dao.module.service_module_dao import ServiceModuleVersionDao
from app.schemas.product.product_schemas import FeatureCreate
from app.services.exceptions import ServiceException, NotFoundError

class FeatureManager:
    """
    [系统层] Feature管理的核心业务逻辑。
    """
    def __init__(self, db: AsyncSession):
        self.db = db
        self.feature_dao = FeatureDao(db)
        self.smv_dao = ServiceModuleVersionDao(db)

    async def create_feature(self, feature_data: FeatureCreate) -> Feature:
        """创建一个新的 Feature。"""
        if await self.feature_dao.get_one(where={"name": feature_data.name}):
            raise ServiceException(f"Feature with name '{feature_data.name}' already exists.")

        smv_id = None
        if feature_data.service_module_version_name:
            smv = await self.smv_dao.get_one(where={"name": feature_data.service_module_version_name})
            if not smv:
                raise NotFoundError(f"ServiceModuleVersion '{feature_data.service_module_version_name}' not found.")
            smv_id = smv.id

        feature_dict = feature_data.model_dump(exclude={"service_module_version_name"})
        new_feature = Feature(**feature_dict, service_module_version_id=smv_id)
        
        return await self.feature_dao.add(new_feature)