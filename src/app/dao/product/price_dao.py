# src/app/dao/product/price_dao.py

from sqlalchemy.ext.asyncio import AsyncSession
from app.dao.base_dao import BaseDao
from app.models.product import Price

class PriceDao(BaseDao[Price]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(Price, db_session)