from typing import List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.dao.base_dao import BaseDao
from app.models.asset import AssetFolder


class AssetFolderDao(BaseDao[AssetFolder]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(AssetFolder, db_session)

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[AssetFolder]:
        return await self.get_one(where={"uuid": uuid}, withs=withs)

    async def get_active_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[AssetFolder]:
        return await self.get_one(where={"uuid": uuid, "is_deleted": False}, withs=withs)

    async def soft_delete(self, uuid: str) -> bool:
        stmt = (
            update(AssetFolder)
            .where(AssetFolder.uuid == uuid)
            .values(is_deleted=True, deleted_at=func.now())
        )
        result = await self.db_session.execute(stmt)
        return result.rowcount > 0

    async def list_roots(self, workspace_id: int) -> List[AssetFolder]:
        filters = [
            AssetFolder.workspace_id == workspace_id,
            AssetFolder.parent_id.is_(None),
            AssetFolder.is_deleted == False,
        ]
        return await self.get_list(where=filters, withs=["parent"], order=[AssetFolder.name.asc()])

    async def list_children(self, parent_id: int) -> List[AssetFolder]:
        return await self.get_list(
            where={"parent_id": parent_id, "is_deleted": False},
            withs=["parent"],
            order=[AssetFolder.name.asc()],
        )

    async def list_by_workspace(self, workspace_id: int) -> List[AssetFolder]:
        return await self.get_list(
            where={"workspace_id": workspace_id, "is_deleted": False},
            withs=["parent"],
            order=[AssetFolder.name.asc()],
        )

    async def has_name_conflict(
        self,
        *,
        workspace_id: int,
        parent_id: Optional[int],
        name: str,
        exclude_id: Optional[int] = None,
    ) -> bool:
        conditions = [
            AssetFolder.workspace_id == workspace_id,
            AssetFolder.is_deleted == False,
            AssetFolder.name == name.strip(),
        ]
        if parent_id is None:
            conditions.append(AssetFolder.parent_id.is_(None))
        else:
            conditions.append(AssetFolder.parent_id == parent_id)

        if exclude_id is not None:
            conditions.append(AssetFolder.id != exclude_id)

        stmt = select(func.count(AssetFolder.id)).where(*conditions)
        count = int((await self.db_session.execute(stmt)).scalar_one() or 0)
        return count > 0
