# src/app/dao/resource/workflow/workflow_dao.py

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload, joinedload, load_only
from typing import Optional
from app.dao.base_dao import BaseDao
from app.models.resource.workflow import Workflow, WorkflowNodeDef
from app.models.resource import Resource
from app.models.workspace import Workspace
from app.models.identity import User, Team

class WorkflowDao(BaseDao[Workflow]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(Workflow, db_session)

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[Workflow]:
        resource_loader = joinedload(Workflow.resource).options(
            lazyload("*"),
            load_only(
                Resource.id,
                Resource.uuid,
                Resource.workspace_id,
                Resource.resource_type_id
            ),
            joinedload(Resource.workspace).options(
                lazyload("*"),
                load_only(
                    Workspace.id,
                    Workspace.uuid,
                    Workspace.owner_user_id,
                    Workspace.owner_team_id
                ),
                joinedload(Workspace.user_owner).options(
                    joinedload(User.billing_account)
                ),
                joinedload(Workspace.team).options(
                    joinedload(Team.billing_account)
                ),
            )
        )
        return await self.get_one(
            where={"uuid": uuid},
            withs=withs,
            options=[
                lazyload("*"),
                resource_loader,
                joinedload(Workflow.creator).options(
                    lazyload("*"),
                    load_only(User.id, User.uuid, User.nick_name, User.avatar)
                ),
            ]
        )

class WorkflowNodeDefDao(BaseDao[WorkflowNodeDef]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(WorkflowNodeDef, db_session)
