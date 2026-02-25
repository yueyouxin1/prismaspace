from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from app.dao.base_dao import BaseDao
from app.models.permission import Role, RolePermission

class RoleDao(BaseDao[Role]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(Role, db_session)

    async def get_system_role_by_name(self, name: str, withs: Optional[list] = None) -> Role | None:
        """
        [核心方法] 通过名称获取系统级预设角色。
        系统角色的 team_id 为 NULL。
        """
        return await self.get_one(where={"name": name, "team_id": None}, withs=withs)

class RolePermissionDao(BaseDao[RolePermission]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(model_class=RolePermission, db_session=db_session)