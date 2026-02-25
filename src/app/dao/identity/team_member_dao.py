# app/dao/identity/team_member_dao.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload
from typing import List, Optional
from app.dao.base_dao import BaseDao
from app.models.identity import TeamMember

class TeamMemberDao(BaseDao[TeamMember]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(TeamMember, db_session)

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[TeamMember]:
        """Finds a team member by their UUID."""
        return await self.get_one(where={"uuid": uuid}, withs=withs)

    async def get_members_by_team_id(self, team_id: int) -> List[TeamMember]:
        """获取一个团队的所有成员，并预加载用户信息和角色信息。"""
        stmt = (
            select(self.model)
            .where(self.model.team_id == team_id)
            .options(
                joinedload(self.model.user),
                joinedload(self.model.role)
            )
        )
        result = await self.db_session.execute(stmt)
        return list(result.scalars().all())