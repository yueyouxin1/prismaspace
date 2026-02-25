# src/app/services/product/feature_service.py

from typing import List
from app.core.context import AppContext
from app.schemas.product.product_schemas import FeatureCreate, FeatureRead
from app.services.base_service import BaseService
from app.services.exceptions import NotFoundError
from app.system.product.feature_manager import FeatureManager

class FeatureService(BaseService):
    """
    [服务层] 负责Feature管理的业务流程编排和权限检查。
    """
    def __init__(self, context: AppContext):
        self.context = context
        self.db = context.db
        self.manager = FeatureManager(context.db)

    async def create_feature(self, feature_data: FeatureCreate) -> FeatureRead:
        """[Admin] 创建一个新的 Feature。"""
        await self.context.perm_evaluator.ensure_can(["platform:product:manage"])
        
        new_feature = await self.manager.create_feature(feature_data)
        
        # 重新获取以加载关联关系
        final_feature = await self.manager.get_feature_by_name(new_feature.name)
        return FeatureRead.model_validate(final_feature)

    async def get_feature_by_name(self, name: str) -> FeatureRead:
        """[Admin] 按名称获取一个 Feature 的详情。"""
        await self.context.perm_evaluator.ensure_can(["platform:product:manage"])
        
        feature = await self.manager.dao.get_by_name(name ,withs=["service_module_version"])
        if not feature:
            raise NotFoundError(f"Feature with name '{name}' not found.")
        return FeatureRead.model_validate(feature)

    async def list_features(self) -> List[FeatureRead]:
        """[Admin] 获取所有 Features 列表。"""
        await self.context.perm_evaluator.ensure_can(["platform:product:manage"])

        features = await self.manager.feature_dao.get_list(withs=["service_module_version"])
        return [FeatureRead.model_validate(f) for f in features]