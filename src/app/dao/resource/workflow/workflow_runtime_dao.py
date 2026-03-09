from typing import Optional

from sqlalchemy import desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.dao.base_dao import BaseDao
from app.models.resource.workflow import WorkflowExecutionCheckpoint, WorkflowNodeExecution


class WorkflowExecutionCheckpointDao(BaseDao[WorkflowExecutionCheckpoint]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(WorkflowExecutionCheckpoint, db_session)

    async def get_latest_by_execution_id(
        self,
        resource_execution_id: int,
    ) -> Optional[WorkflowExecutionCheckpoint]:
        return await self.get_one(
            where={"resource_execution_id": resource_execution_id},
            order=[
                desc(WorkflowExecutionCheckpoint.step_index),
                desc(WorkflowExecutionCheckpoint.id),
            ],
        )


class WorkflowNodeExecutionDao(BaseDao[WorkflowNodeExecution]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(WorkflowNodeExecution, db_session)

    async def get_by_execution_node_attempt(
        self,
        *,
        resource_execution_id: int,
        node_id: str,
        attempt: int = 1,
    ) -> Optional[WorkflowNodeExecution]:
        return await self.get_one(
            where={
                "resource_execution_id": resource_execution_id,
                "node_id": node_id,
                "attempt": attempt,
            }
        )
