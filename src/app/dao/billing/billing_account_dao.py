from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import joinedload

from typing import Optional
from app.dao.base_dao import BaseDao
from app.models.billing import BillingAccount, PaymentGateway, PaymentMethod, CreditCardPaymentMethod, AlipayPaymentMethod

class BillingAccountDao(BaseDao[BillingAccount]):
    def __init__(self, session: AsyncSession):
        super().__init__(model_class=BillingAccount, db_session=session)

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[BillingAccount]:
        """Finds a billing account by their UUID."""
        return await self.get_one(where={"uuid": uuid}, withs=withs)

class PaymentGatewayDao(BaseDao[PaymentGateway]):
    def __init__(self, session: AsyncSession):
        super().__init__(model_class=PaymentGateway, db_session=session)

class PaymentMethodDao(BaseDao[PaymentMethod]):
    def __init__(self, session: AsyncSession):
        super().__init__(model_class=PaymentMethod, db_session=session)

class CreditCardPaymentMethodDao(BaseDao[CreditCardPaymentMethod]):
    def __init__(self, session: AsyncSession):
        super().__init__(model_class=CreditCardPaymentMethod, db_session=session)

class AlipayPaymentMethodDao(BaseDao[AlipayPaymentMethod]):
    def __init__(self, session: AsyncSession):
        super().__init__(model_class=AlipayPaymentMethod, db_session=session)