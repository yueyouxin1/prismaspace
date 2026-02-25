# src/app/dao/resource/tool/tool_dao.py

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload, joinedload, load_only
from typing import Optional
from app.dao.base_dao import BaseDao
from app.models.product import Feature, Product, Price
from app.models.resource.tool import Tool
from app.models.resource import Resource
from app.models.workspace import Workspace
from app.models.identity import User, Team

class ToolDao(BaseDao[Tool]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(Tool, db_session)

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[Tool]:
        """Finds a Tool instance by its UUID."""
        resource_loader = joinedload(Tool.resource).options(
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
                joinedload(Tool.creator).options(
                    lazyload("*"),
                    load_only(User.id, User.uuid, User.nick_name, User.avatar)
                ),
                joinedload(Tool.linked_feature).options(
                    lazyload("*"),
                    joinedload(Feature.product).options(
                        lazyload("*"),
                        joinedload(Product.prices).options(
                            lazyload("*"),
                            joinedload(Price.tiers)
                        )
                    )
                ),
            ]
        )
