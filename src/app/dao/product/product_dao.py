# src/app/dao/product/product_dao.py

from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from app.dao.base_dao import BaseDao
from app.models.product import Product

class ProductDao(BaseDao[Product]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(Product, db_session)

    async def get_by_name(self, name: str, withs: Optional[list] = None) -> Product | None:
        return await self.get_one(where={"name": name}, withs=withs)