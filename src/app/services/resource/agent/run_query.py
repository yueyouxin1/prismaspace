from __future__ import annotations

from typing import AsyncGenerator, List, Optional

from app.schemas.resource.agent.agent_schemas import AgentRunDetailRead, AgentRunEventRead, AgentRunSummaryRead
from app.services.exceptions import NotFoundError
from app.services.resource.agent.run_control import AgentRunRegistry


class AgentRunQueryService:
    """
    负责 Agent run 查询面。
    """

    def __init__(self, agent_service):
        self.agent_service = agent_service

    async def list_runs(self, instance_uuid: str, *, limit: int = 20) -> List[AgentRunSummaryRead]:
        service = self.agent_service
        instance = await service.get_by_uuid(instance_uuid)
        if not instance:
            raise NotFoundError("Agent not found")
        await service._check_execute_perm(instance)
        rows = await service.execution_ledger_service.dao.get_list(
            where={"resource_instance_id": instance.id},
            order=[
                service.execution_ledger_service.dao.model.created_at.desc(),
                service.execution_ledger_service.dao.model.id.desc(),
            ],
            limit=limit,
        )
        return [service.run_persistence_service.build_run_summary(execution=row) for row in rows]

    async def get_active_run(
        self,
        *,
        instance_uuid: str,
        actor,
        thread_id: str,
    ) -> Optional[AgentRunSummaryRead]:
        service = self.agent_service
        instance = await service.get_by_uuid(instance_uuid)
        if not instance:
            raise NotFoundError("Agent not found")
        await service._check_execute_perm(instance)
        execution = await service.execution_ledger_service.get_latest_active_execution(
            instance=instance,
            actor=actor,
            thread_id=thread_id,
        )
        if execution is None:
            return None
        return service.run_persistence_service.build_run_summary(execution=execution)

    async def get_run(self, run_id: str) -> AgentRunDetailRead:
        service = self.agent_service
        execution = await service.execution_ledger_service.get_by_run_id(run_id)
        if execution is None:
            raise NotFoundError("Agent run not found.")
        instance = await service.dao.get_by_pk(execution.resource_instance_id)
        if instance is None:
            raise NotFoundError("Agent instance not found.")
        await service._check_execute_perm(instance)
        checkpoint = await service.run_persistence_service.get_checkpoint(execution_id=execution.id)
        events = await service.run_persistence_service.list_events(execution_id=execution.id)
        tool_executions = await service.run_persistence_service.list_tool_executions(execution_id=execution.id)
        return service.run_persistence_service.build_run_detail(
            execution=execution,
            agent_instance=instance,
            checkpoint=checkpoint,
            events=events,
            tool_executions=tool_executions,
        )

    async def list_run_events(self, run_id: str, *, limit: int = 1000) -> List[AgentRunEventRead]:
        service = self.agent_service
        execution = await service.execution_ledger_service.get_by_run_id(run_id)
        if execution is None:
            raise NotFoundError("Agent run not found.")
        instance = await service.dao.get_by_pk(execution.resource_instance_id)
        if instance is None:
            raise NotFoundError("Agent instance not found.")
        await service._check_execute_perm(instance)
        return await service.run_persistence_service.list_events(execution_id=execution.id, limit=limit)

    async def stream_live_events(
        self,
        *,
        run_id: str,
        after_seq: int = 0,
    ) -> AsyncGenerator[dict, None]:
        service = self.agent_service
        execution = await service.execution_ledger_service.get_by_run_id(run_id)
        if execution is None:
            raise NotFoundError("Agent run not found.")
        instance = await service.dao.get_by_pk(execution.resource_instance_id)
        if instance is None:
            raise NotFoundError("Agent instance not found.")
        await service._check_execute_perm(instance)

        async for envelope in service.live_event_service.stream_events(run_id, after_seq=after_seq):
            yield envelope

    async def cancel_run(self, run_id: str) -> dict:
        service = self.agent_service
        execution = await service.execution_ledger_service.get_by_run_id(run_id)
        if execution is None:
            raise NotFoundError("Agent run not found.")
        instance = await service.dao.get_by_pk(execution.resource_instance_id)
        if instance is None:
            raise NotFoundError("Agent instance not found.")
        await service._check_execute_perm(instance)
        await service.run_control_service.request_cancel(run_id)
        local_cancelled = AgentRunRegistry.cancel(run_id)
        return {
            "run_id": run_id,
            "accepted": True,
            "local_cancelled": local_cancelled,
        }
