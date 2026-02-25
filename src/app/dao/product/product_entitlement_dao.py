# src/app/dao/product/product_entitlement_dao.py

from sqlalchemy.ext.asyncio import AsyncSession
from app.dao.base_dao import BaseDao
from app.models.product import ProductEntitlement

class ProductEntitlementDao(BaseDao[ProductEntitlement]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(ProductEntitlement, db_session)