# app/dao/project_dao/project_dao.py

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import joinedload

from typing import Optional, List
from app.dao.base_dao import BaseDao
from app.models.workspace import Project
from app.models.resource import Resource, ResourceType

class ProjectDao(BaseDao[Project]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(Project, db_session)

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[Project]:
        """Finds a project by their UUID."""
        return await self.get_one(where={"uuid": uuid}, withs=withs)

    async def get_projects_by_workspace_id(
        self,
        workspace_id: int,
        main_application_type: Optional[str] = None
    ) -> List[Project]:
        """获取指定工作空间下的所有项目，并支持主应用类型筛选。"""
        stmt = (
            select(Project)
            .where(Project.workspace_id == workspace_id)
            .outerjoin(Project.main_resource)
            .outerjoin(Resource.resource_type)
            .options(
                joinedload(Project.creator),
                joinedload(Project.main_resource).joinedload(Resource.resource_type),
            )
            .order_by(Project.created_at.desc())
        )

        if main_application_type == "unset":
            stmt = stmt.where(
                or_(
                    Project.main_resource_id.is_(None),
                    ResourceType.name.not_in(["uiapp", "agent"]),
                )
            )
        elif main_application_type:
            stmt = stmt.where(ResourceType.name == main_application_type)

        result = await self.db_session.execute(stmt)
        return result.scalars().unique().all()
