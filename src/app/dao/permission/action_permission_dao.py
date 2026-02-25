from sqlalchemy.ext.asyncio import AsyncSession
from app.dao.base_dao import BaseDao
from app.models.permission import ActionPermission

class ActionPermissionDao(BaseDao[ActionPermission]):
    def __init__(self, session: AsyncSession):
        super().__init__(model_class=ActionPermission, db_session=session)