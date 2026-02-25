# src/app/dao/resource/tenantdb/tenantdb_dao.py

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload, joinedload, load_only
from typing import Optional
from app.dao.base_dao import BaseDao
from app.models.identity import User, Team
from app.models.workspace import Workspace
from app.models.resource import Resource
from app.models.resource.tenantdb import TenantDB, TenantTable, TenantColumn

class TenantDBDao(BaseDao[TenantDB]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(TenantDB, db_session)

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[TenantDB]:
        """Finds a TenantDB instance by its UUID."""
        resource_loader = joinedload(TenantDB.resource).options(
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
                joinedload(TenantDB.creator).options(
                    lazyload("*"),
                    load_only(User.id, User.uuid, User.nick_name, User.avatar)
                ),
            ]
        )

class TenantTableDao(BaseDao[TenantTable]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(TenantTable, db_session)

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[TenantTable]:
        """Finds a TenantTable by its UUID."""
        return await self.get_one(where={"uuid": uuid}, withs=withs)

class TenantColumnDao(BaseDao[TenantColumn]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(TenantColumn, db_session)

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[TenantColumn]:
        """Finds a TenantColumn by its UUID."""
        return await self.get_one(where={"uuid": uuid}, withs=withs)
