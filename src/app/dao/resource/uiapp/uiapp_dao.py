# src/app/dao/resource/uiapp/uiapp_dao.py

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload, lazyload, joinedload, load_only
from typing import Optional
from app.dao.base_dao import BaseDao
from app.models.identity import User, Team
from app.models.workspace import Workspace
from app.models.resource import Resource
from app.models.resource.uiapp import UiApp, UiPage

class UiAppDao(BaseDao[UiApp]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(UiApp, db_session)

    async def get_by_uuid(self, uuid: str) -> Optional[UiApp]:
        """
        获取 App 骨架，预加载页面列表（元数据）。
        注意：SQLAlchemy 的 selectinload 会加载关联对象的所有字段。
        为了性能，我们可能需要 defer 加载 UiPage.data，但这在 ORM 层比较复杂。
        策略：由于 UiPage 是独立表，selectinload 是一次额外查询。
        如果 data 字段巨大，建议使用 load_only 排除 data 字段。
        """
        stmt = (
            select(UiApp)
            .where(UiApp.uuid == uuid)
            .options(
                lazyload("*"),
                joinedload(UiApp.resource).options(
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
                ),
                joinedload(UiApp.creator).options(
                    lazyload("*"),
                    load_only(User.id, User.uuid, User.nick_name, User.avatar)
                ),
                # 仅加载页面的元数据，排除 heavy data
                selectinload(UiApp.pages).load_only(
                    UiPage.page_uuid, UiPage.path, UiPage.label, 
                    UiPage.icon, UiPage.display_order, UiPage.config
                )
            )
        )
        result = await self.db_session.execute(stmt)
        return result.scalars().first()

    async def get_full_by_uuid(self, uuid: str) -> Optional[UiApp]:
        """[Export] 获取完整 App，包含所有页面的 DSL"""
        return await self.get_one(where={"uuid": uuid}, withs=["pages"])

class UiPageDao(BaseDao[UiPage]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(UiPage, db_session)

    async def get_by_app_and_page_uuid(self, app_version_id: int, page_uuid: str) -> Optional[UiPage]:
        return await self.get_one(
            where={"app_version_id": app_version_id, "page_uuid": page_uuid}
        )

    async def delete_by_app_version(self, app_version_id: int):
        stmt = delete(UiPage).where(UiPage.app_version_id == app_version_id)
        await self.db_session.execute(stmt)
