# src/app/system/product/product_entitlement_manager.py

from sqlalchemy.ext.asyncio import AsyncSession
from app.models import ProductEntitlement, Feature
from app.dao.product.product_entitlement_dao import ProductEntitlementDao
from app.dao.product.feature_dao import FeatureDao
from app.schemas.product.product_schemas import ProductEntitlementCreate
from app.services.exceptions import NotFoundError

class ProductEntitlementManager:
    """[System Layer] Manages the core business logic for ProductEntitlements."""
    def __init__(self, db: AsyncSession):
        self.db = db
        self.entitlement_dao = ProductEntitlementDao(db)
        self.feature_dao = FeatureDao(db)

    async def create_entitlement_for_product(self, product_id: int, entitlement_data: ProductEntitlementCreate) -> ProductEntitlement:
        """Creates a new ProductEntitlement, linking a Product to a Feature."""
        feature = await self.feature_dao.get_one(where={"name": entitlement_data.feature_name})
        if not feature:
            raise NotFoundError(f"Feature '{entitlement_data.feature_name}' not found.")

        new_entitlement = ProductEntitlement(
            product_id=product_id,
            feature_id=feature.id,
            quota=entitlement_data.quota,
            is_resettable=entitlement_data.is_resettable
        )
        return await self.entitlement_dao.add(new_entitlement)