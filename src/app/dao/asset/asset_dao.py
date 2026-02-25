# src/app/dao/asset/asset_dao.py

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func, and_, or_
from typing import List, Optional, Union

from app.dao.base_dao import BaseDao
from app.models import User, Team
from app.models.asset import Asset, AssetStatus, AssetType

class AssetDao(BaseDao[Asset]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(Asset, db_session)

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[Asset]:
        return await self.get_one(where={"uuid": uuid}, withs=withs)

    async def get_active_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[Asset]:
        """覆盖查询，确保只查未删除的"""
        return await self.get_one(where={"uuid": uuid, "is_deleted": False}, withs=withs)

    async def soft_delete(self, uuid: str) -> bool:
        """执行软删除"""
        stmt = (
            update(Asset)
            .where(Asset.uuid == uuid)
            .values(is_deleted=True, deleted_at=func.now())
        )
        result = await self.db_session.execute(stmt)
        return result.rowcount > 0

    async def list_by_folder(
        self, 
        workspace_id: int, 
        folder_id: Optional[int], 
        asset_type: Optional[AssetType] = None,
        page: int = 1, 
        limit: int = 20
    ) -> List[Asset]:
        """
        List assets in a specific folder (or root) for a specific owner.
        """
        filters = [Asset.workspace_id == workspace_id, Asset.is_deleted == False] # 默认加这个过滤
            
        # Folder filter
        if folder_id is not None:
            filters.append(Asset.folder_id == folder_id)
        else:
            filters.append(Asset.folder_id.is_(None)) # Root folder
            
        # Type filter
        if asset_type:
            filters.append(Asset.type == asset_type)
            
        # Exclude FAILED assets by default in listings? Maybe user wants to see them to retry.
        # Let's show all but order by active.
        
        return await self.get_list(
            where=filters,
            order=[Asset.created_at.desc()],
            page=page,
            limit=limit
        )