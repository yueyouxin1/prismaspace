# src/app/dao/resource/agent/agent_dao.py

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload, joinedload, load_only
from typing import Optional
from app.dao.base_dao import BaseDao
from app.models.product import Feature, Product, Price
from app.models.resource.agent import Agent
from app.models.resource import Resource
from app.models.workspace import Workspace
from app.models.identity import User, Team

class AgentDao(BaseDao[Agent]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(Agent, db_session)

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[Agent]:
        resource_loader = joinedload(Agent.resource).options(
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
                joinedload(Agent.creator).options(
                    lazyload("*"),
                    load_only(User.id, User.uuid, User.nick_name, User.avatar)
                ),
                joinedload(Agent.linked_feature).options(
                    lazyload("*"),
                    joinedload(Feature.product).options(
                        lazyload("*"),
                        joinedload(Product.prices).options(
                            lazyload("*"),
                            joinedload(Price.tiers)
                        )
                    )
                ),
                joinedload(Agent.llm_module_version),
            ]
        )
