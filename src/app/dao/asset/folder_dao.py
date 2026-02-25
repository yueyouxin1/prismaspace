# src/app/dao/asset/folder_dao.py

from typing import List, Optional, Union
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.dao.base_dao import BaseDao
from app.models import User, Team
from app.models.asset import AssetFolder

class AssetFolderDao(BaseDao[AssetFolder]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(AssetFolder, db_session)

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[AssetFolder]:
        return await self.get_one(where={"uuid": uuid}, withs=withs)

    async def get_active_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[AssetFolder]:
        """覆盖查询，确保只查未删除的"""
        return await self.get_one(where={"uuid": uuid, "is_deleted": False}, withs=withs)

    async def soft_delete(self, uuid: str) -> bool:
        """执行软删除"""
        stmt = (
            update(AssetFolder)
            .where(AssetFolder.uuid == uuid)
            .values(is_deleted=True, deleted_at=func.now())
        )
        result = await self.db_session.execute(stmt)
        return result.rowcount > 0

    async def list_roots(self, workspace_id: int) -> List[AssetFolder]:
        """List root folders for an owner."""
        filters = [AssetFolder.workspace_id == workspace_id, AssetFolder.parent_id.is_(None), AssetFolder.is_deleted == False]
            
        return await self.get_list(where=filters, order=[AssetFolder.name.asc()])

    async def list_children(self, parent_id: int) -> List[AssetFolder]:
        return await self.get_list(
            where={"parent_id": parent_id, "is_deleted": False},
            order=[AssetFolder.name.asc()]
        )