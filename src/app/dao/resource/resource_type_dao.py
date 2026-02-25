# app/dao/resource/resource_dao.py

from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from app.dao.base_dao import BaseDao
from app.models.resource import ResourceType

class ResourceTypeDao(BaseDao[ResourceType]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(ResourceType, db_session)