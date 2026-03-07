from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.dao.base_dao import BaseDao
from app.models.resource.execution import ResourceExecution


class ResourceExecutionDao(BaseDao[ResourceExecution]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(ResourceExecution, db_session)

    async def get_by_run_id(self, run_id: str) -> Optional[ResourceExecution]:
        return await self.get_one(where={"run_id": run_id})
