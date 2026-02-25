# src/app/dao/identity/membership_dao.py

from sqlalchemy.ext.asyncio import AsyncSession
from app.dao.base_dao import BaseDao
from app.models import Membership, MembershipHistory

class MembershipDao(BaseDao[Membership]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(Membership, db_session)

class MembershipHistoryDao(BaseDao[MembershipHistory]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(MembershipHistory, db_session)