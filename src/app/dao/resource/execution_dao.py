from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import desc

from app.dao.base_dao import BaseDao
from app.models.resource.execution import ResourceExecution


class ResourceExecutionDao(BaseDao[ResourceExecution]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(ResourceExecution, db_session)

    async def get_by_run_id(self, run_id: str) -> Optional[ResourceExecution]:
        return await self.get_one(where={"run_id": run_id})

    async def get_latest_active_by_instance_user_thread(
        self,
        *,
        resource_instance_id: int,
        user_id: int,
        thread_id: str,
    ) -> Optional[ResourceExecution]:
        return await self.get_one(
            where={
                "resource_instance_id": resource_instance_id,
                "user_id": user_id,
                "thread_id": thread_id,
            },
            where_or=None,
            order=[desc(ResourceExecution.created_at), desc(ResourceExecution.id)],
        )
