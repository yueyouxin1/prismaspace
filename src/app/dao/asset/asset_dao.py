from typing import List, Optional, Sequence, Tuple

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.dao.base_dao import BaseDao
from app.models.asset import Asset, AssetType


class AssetDao(BaseDao[Asset]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(Asset, db_session)

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[Asset]:
        return await self.get_one(where={"uuid": uuid}, withs=withs)

    async def get_active_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[Asset]:
        return await self.get_one(where={"uuid": uuid, "is_deleted": False}, withs=withs)

    async def soft_delete(self, uuid: str) -> bool:
        stmt = (
            update(Asset)
            .where(Asset.uuid == uuid)
            .values(is_deleted=True, deleted_at=func.now())
        )
        result = await self.db_session.execute(stmt)
        return result.rowcount > 0

    async def list_by_workspace(
        self,
        workspace_id: int,
        *,
        folder_ids: Optional[Sequence[int]] = None,
        asset_type: Optional[AssetType] = None,
        keyword: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
    ) -> Tuple[List[Asset], int]:
        filters = [Asset.workspace_id == workspace_id, Asset.is_deleted == False]

        if folder_ids is not None:
            if len(folder_ids) == 0:
                return [], 0
            filters.append(Asset.folder_id.in_(folder_ids))

        if asset_type:
            filters.append(Asset.type == asset_type)

        if keyword:
            normalized = f"%{keyword.strip()}%"
            filters.append(
                or_(
                    Asset.name.ilike(normalized),
                    Asset.mime_type.ilike(normalized),
                    Asset.real_name.ilike(normalized),
                )
            )

        count_stmt = select(func.count(Asset.id)).where(*filters)
        total = int((await self.db_session.execute(count_stmt)).scalar_one() or 0)

        items = await self.get_list(
            where=filters,
            withs=["folder", "intelligence"],
            order=[Asset.created_at.desc()],
            page=page,
            limit=limit,
        )
        return items, total
