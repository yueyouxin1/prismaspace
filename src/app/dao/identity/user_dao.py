# app/dao/identity/user_dao.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import joinedload

from typing import Optional
from app.dao.base_dao import BaseDao
from app.models.identity import User

class UserDao(BaseDao[User]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(User, db_session)
    
    async def get_by_email(self, email: str, withs: Optional[list] = None) -> Optional[User]:
        """Finds a user by their email address."""
        return await self.get_one(where={"email": email}, withs=withs)

    async def get_by_phone_number(self, phone_number: str, withs: Optional[list] = None) -> Optional[User]:
        """Finds a user by their phone number."""
        return await self.get_one(where={"phone_number": phone_number}, withs=withs)
    
    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[User]:
        """Finds a user by their UUID."""
        return await self.get_one(where={"uuid": uuid}, withs=withs)