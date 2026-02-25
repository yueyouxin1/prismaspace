# src/app/dao/billing/consumption_record_dao.py
from sqlalchemy.ext.asyncio import AsyncSession
from app.dao.base_dao import BaseDao
from app.models.billing import ConsumptionRecord

class ConsumptionRecordDao(BaseDao[ConsumptionRecord]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(ConsumptionRecord, db_session)