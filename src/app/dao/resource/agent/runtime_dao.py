from typing import Optional

from sqlalchemy import desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.dao.base_dao import BaseDao
from app.models.resource.agent import AgentRunCheckpoint, AgentRunEvent, AgentToolExecution


class AgentRunEventDao(BaseDao[AgentRunEvent]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(AgentRunEvent, db_session)

    async def get_last_event(
        self,
        *,
        resource_execution_id: int,
    ) -> Optional[AgentRunEvent]:
        return await self.get_one(
            where={"resource_execution_id": resource_execution_id},
            order=[desc(AgentRunEvent.sequence_no)],
        )


class AgentToolExecutionDao(BaseDao[AgentToolExecution]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(AgentToolExecution, db_session)

    async def get_by_run_and_tool_call(
        self,
        *,
        resource_execution_id: int,
        tool_call_id: str,
    ) -> Optional[AgentToolExecution]:
        return await self.get_one(
            where={
                "resource_execution_id": resource_execution_id,
                "tool_call_id": tool_call_id,
            }
        )


class AgentRunCheckpointDao(BaseDao[AgentRunCheckpoint]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(AgentRunCheckpoint, db_session)

    async def get_by_execution_id(
        self,
        *,
        resource_execution_id: int,
    ) -> Optional[AgentRunCheckpoint]:
        return await self.get_one(where={"resource_execution_id": resource_execution_id})
