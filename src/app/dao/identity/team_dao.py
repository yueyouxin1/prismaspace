from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import joinedload

from typing import List, Optional
from app.dao.base_dao import BaseDao
from app.models.identity import Team, TeamMember, Invitation

class TeamDao(BaseDao[Team]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(Team, db_session)

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[Team]:
        """Finds a team by their UUID."""
        return await self.get_one(where={"uuid": uuid}, withs=withs)

    async def get_all_for_user(self, user_id: int) -> List[Team]:
        """获取一个用户作为成员所在的所有团队。"""
        stmt = (
            select(Team)
            .join(TeamMember, Team.id == TeamMember.team_id)
            .where(TeamMember.user_id == user_id)
            .options(joinedload(Team.owner)) # 预加载所有者信息
        )
        result = await self.db_session.execute(stmt)
        return list(result.scalars().all())

class InvitationDao(BaseDao[Invitation]):
    def __init__(self, session: AsyncSession):
        super().__init__(model_class=Invitation, db_session=db_session)

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[Invitation]:
        """Finds a invitation by their UUID."""
        return await self.get_one(where={"uuid": uuid}, withs=withs)