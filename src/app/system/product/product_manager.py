# src/app/system/product/product_manager.py

from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from app.models import Product, ProductType, Role, Feature
from app.dao.product.product_dao import ProductDao
from app.dao.permission.role_dao import RoleDao
from app.dao.product.feature_dao import FeatureDao
from app.schemas.product.product_schemas import ProductCreateFull, ProductCreate, ProductUpdate
from .price_manager import PriceManager
from .product_entitlement_manager import ProductEntitlementManager
from app.services.exceptions import ServiceException, NotFoundError

class ProductManager:
    """[System Layer] Manages the core business logic for Products."""
    def __init__(self, db: AsyncSession):
        self.db = db
        self.dao = ProductDao(db)
        self.role_dao = RoleDao(db)
        self.feature_dao = FeatureDao(db)
        self.price_manager = PriceManager(db)
        self.entitlement_manager = ProductEntitlementManager(db)

    async def _validate_common(self, data: ProductCreate):
        if await self.dao.get_one(where={"name": data.name}):
            raise ServiceException(f"Product with name '{data.name}' already exists.")

    async def _prepare_membership_product(self, data: ProductCreate) -> dict:
        role = await self.role_dao.get_system_role_by_name(data.granted_role_name)
        if not role:
            raise NotFoundError(f"System role '{data.granted_role_name}' not found.")
        return {"granted_role_id": role.id}

    async def _prepare_usage_product(self, data: ProductCreate) -> dict:
        feature = await self.feature_dao.get_one(where={"name": data.feature_name})
        if not feature:
            raise NotFoundError(f"Feature '{data.feature_name}' not found.")
        return {"feature_id": feature.id}

    async def create_full_product(self, data: ProductCreateFull) -> Product:
        """
        [核心业务逻辑] 在一个事务中原子地创建产品及其关联的价格和权益。
        """
        # 注意：这里不再需要 begin_nested，因为调用方（Service或Seed）会管理顶级事务
        
        # 1. 创建核心产品对象，但不包括嵌套列表
        # 我们需要从 ProductCreateFull 适配到 ProductCreate 的数据
        product_create_data = data.model_dump(exclude={'prices', 'entitlements'})
        new_product = await self.create_product(ProductCreate(**product_create_data))

        # 2. 循环创建关联的价格
        for price_data in data.prices:
            await self.price_manager.create_price_for_product(new_product.id, price_data)

        # 3. 循环创建关联的权益
        for entitlement_data in data.entitlements:
            await self.entitlement_manager.create_entitlement_for_product(new_product.id, entitlement_data)
        
        # 刷新以加载新创建的关系
        await self.db.refresh(new_product, attribute_names=['prices', 'entitlements'])
        return new_product

    async def create_product(self, product_data: ProductCreate) -> Product:
        await self._validate_common(product_data)

        extra_attrs = {}
        if product_data.type == ProductType.MEMBERSHIP:
            extra_attrs = await self._prepare_membership_product(product_data)
        elif product_data.type == ProductType.USAGE:
            extra_attrs = await self._prepare_usage_product(product_data)
        
        create_dict = product_data.model_dump(exclude={'granted_role_name', 'feature_name'}, exclude_none=True)
        create_dict.update(extra_attrs)
        
        new_product = Product(**create_dict)
        return await self.dao.add(new_product)

    async def update_product(self, product: Product, update_data: ProductUpdate) -> Product:
        """[New] Handles updating product metadata."""
        update_dict = update_data.model_dump(exclude_unset=True)
        for key, value in update_dict.items():
            setattr(product, key, value)
        
        await self.db.flush()
        await self.db.refresh(product)
        return product
        
    async def get_product_by_name(self, name: str, withs: List = None) -> Product:
        product = await self.dao.get_one(where={"name": name}, withs=withs)
        if not product:
            raise NotFoundError(f"Product '{name}' not found.")
        return product
    
    async def list_public_products(self) -> List[Product]:
        return await self.dao.get_list(
            where={"is_active": True, "is_purchasable": True},
            withs=["prices", {"name": "entitlements", "withs": ["feature"]}]
        )

    async def update_publish_status(self, product: Product, is_purchasable: bool) -> Product:
        product.is_purchasable = is_purchasable
        await self.db.flush()
        await self.db.refresh(product)
        return product