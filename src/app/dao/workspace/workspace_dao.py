# app/dao/workspace/workspace_dao.py

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import joinedload

from typing import Optional
from app.dao.base_dao import BaseDao
from app.models.workspace import Workspace
from app.models.identity import Team, TeamMember

class WorkspaceDao(BaseDao[Workspace]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(Workspace, db_session)

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[Workspace]:
        """Finds a workspace by their UUID."""
        return await self.get_one(where={"uuid": uuid}, withs=withs)

    async def get_all_for_user(self, user_id: int) -> list[Workspace]:
        """
        [核心查询] 获取一个用户有权访问的所有工作空间。
        这包括：
        1. 该用户作为所有者的个人工作空间。
        2. 该用户作为成员所在团队拥有的所有工作空间。
        """
        # 1. 创建一个子查询，获取用户所属的所有团队ID
        team_ids_subquery = select(TeamMember.team_id).where(TeamMember.user_id == user_id).scalar_subquery()

        # 2. 构建主查询
        stmt = (
            select(self.model)
            .where(
                or_(
                    self.model.owner_user_id == user_id,
                    self.model.owner_team_id.in_(team_ids_subquery)
                )
            )
            .options(
                # 预加载所有者信息以避免N+1问题
                joinedload(self.model.user_owner),
                joinedload(self.model.team).joinedload(Team.owner) # 示例，按需加载
            )
            .order_by(self.model.id)
        )
        
        executed = await self.db_session.execute(stmt)
        return list(executed.scalars().all())

    async def get_by_id_with_owner(self, workspace_id: int) -> Workspace | None:
        """
        通过ID获取工作空间，并预加载所有者信息。
        """
        return await self.get_one(
            where={"id": workspace_id},
            withs=["user_owner", "team"]
        )