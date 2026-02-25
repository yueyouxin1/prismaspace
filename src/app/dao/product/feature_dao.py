# src/app/dao/product/feature_dao.py

from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.dao.base_dao import BaseDao
from app.models.product import Feature

class FeatureDao(BaseDao[Feature]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(Feature, db_session)

    async def get_by_name(self, name: str, withs: Optional[list] = None) -> Feature | None:
        return await self.get_one(where={"name": name}, withs=withs)