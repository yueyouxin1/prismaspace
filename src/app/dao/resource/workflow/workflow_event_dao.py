from typing import Optional

from sqlalchemy import desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.dao.base_dao import BaseDao
from app.models.resource.workflow import WorkflowExecutionEvent


class WorkflowExecutionEventDao(BaseDao[WorkflowExecutionEvent]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(WorkflowExecutionEvent, db_session)

    async def get_last_event(
        self,
        *,
        resource_execution_id: int,
    ) -> Optional[WorkflowExecutionEvent]:
        return await self.get_one(
            where={"resource_execution_id": resource_execution_id},
            order=[desc(WorkflowExecutionEvent.sequence_no)],
        )
