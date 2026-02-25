# src/app/services/product/product_service.py

from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from app.core.context import AppContext
from app.services.base_service import BaseService
from app.services.exceptions import PermissionDeniedError, NotFoundError
from app.system.product.product_manager import ProductManager
from app.schemas.product.product_schemas import (
    ProductCreateFull, ProductReadFull, PriceCreate, ProductEntitlementCreate
)

class ProductService(BaseService):
    """[Service Layer] Orchestrates product management and handles authorization."""
    def __init__(self, context: AppContext):
        self.context = context
        self.db = context.db
        self.product_manager = ProductManager(context.db)

    async def create_full_product(self, data: ProductCreateFull) -> ProductReadFull:
        await self.context.perm_evaluator.ensure_can(["platform:product:manage"])

        # --- 修改这里的逻辑 ---
        async with self.db.begin():
            # 直接委托给 Manager 执行核心业务逻辑
            new_product = await self.product_manager.create_full_product(data)

        # Re-fetch the full object with all relationships for the response
        final_product = await self.product_manager.get_product_by_name(
            new_product.name, 
            withs=["prices", {"name": "entitlements", "withs": ["feature"]}]
        )
        if not final_product: # 防御性编程
             raise ServiceException("Failed to retrieve product after creation.")
        return ProductReadFull.model_validate(final_product)
        
    async def list_public_products(self) -> List[ProductReadFull]:
        """[Public] No permission check needed."""
        products = await self.product_manager.list_public_products()
        return [ProductReadFull.model_validate(p) for p in products]

    async def list_all_products_for_admin(self) -> List[ProductReadFull]:
        """[Admin] Lists all products for management."""
        await self.context.perm_evaluator.ensure_can(["platform:product:manage"])
        # In a real app, this would fetch all products, not just public ones
        products = await self.product_manager.dao.get_list(
            withs=["prices", {"name": "entitlements", "withs": ["feature"]}]
        )
        return [ProductReadFull.model_validate(p) for p in products]

    async def publish_product(self, product_name: str) -> ProductReadFull:
        await self.context.perm_evaluator.ensure_can(["platform:product:manage"])
        product = await self.product_manager.get_product_by_name(product_name)
        updated_product = await self.product_manager.update_publish_status(product, is_purchasable=True)
        return ProductReadFull.model_validate(updated_product)

    async def unpublish_product(self, product_name: str) -> ProductReadFull:
        await self.context.perm_evaluator.ensure_can(["platform:product:manage"])
        product = await self.product_manager.get_product_by_name(product_name)
        updated_product = await self.product_manager.update_publish_status(product, is_purchasable=False)
        return ProductReadFull.model_validate(updated_product)