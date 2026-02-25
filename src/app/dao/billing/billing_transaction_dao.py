from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import joinedload

from typing import Optional
from app.dao.base_dao import BaseDao
from app.models.billing import BillingTransaction

class BillingTransactionDao(BaseDao[BillingTransaction]):
    def __init__(self, session: AsyncSession):
        super().__init__(model_class=BillingTransaction, db_session=session)

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[BillingTransaction]:
        """Finds a billing transaction by their UUID."""
        return await self.get_one(where={"uuid": uuid}, withs=withs)