from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.context import AppContext
from app.dao.resource.agent.runtime_dao import AgentRunCheckpointDao, AgentRunEventDao, AgentToolExecutionDao
from app.models import ResourceExecution
from app.models.resource.agent import Agent, AgentRunCheckpoint, AgentRunEvent, AgentToolExecution
from app.schemas.resource.agent.agent_schemas import (
    AgentRunCheckpointRead,
    AgentRunDetailRead,
    AgentRunEventRead,
    AgentRunSummaryRead,
    AgentToolExecutionRead,
)
from app.services.resource.agent.protocol_adapter.base import ProtocolAdaptedRun
from app.engine.model.llm import LLMMessage
from app.services.base_service import BaseService


class AgentRunPersistenceService(BaseService):
    def __init__(self, context: AppContext):
        self.context = context
        self.db = context.db
        self.checkpoint_dao = AgentRunCheckpointDao(self.db)
        self.event_dao = AgentRunEventDao(self.db)
        self.tool_dao = AgentToolExecutionDao(self.db)

    @staticmethod
    def _message_to_json(message: LLMMessage) -> Dict[str, Any]:
        return message.model_dump(mode="json", by_alias=True, exclude_none=True)

    @staticmethod
    def _compact_snapshots(
        *,
        checkpoint_kind: str,
        adapted_snapshot: Dict[str, Any],
        runtime_snapshot: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        if checkpoint_kind in {"prepared", "interrupted"}:
            return adapted_snapshot, runtime_snapshot

        compacted_runtime = {
            "compacted": True,
            "prepared_message_count": len(runtime_snapshot.get("prepared_messages") or []),
            "prepared_tool_count": len(runtime_snapshot.get("prepared_tools") or []),
            "resume_message_count": len(runtime_snapshot.get("resume_message_history") or []),
        }
        return {}, compacted_runtime

    async def upsert_checkpoint(
        self,
        *,
        execution: ResourceExecution,
        agent_instance: Agent,
        session_id: Optional[int],
        thread_id: str,
        turn_id: str,
        checkpoint_kind: str,
        run_input_payload: Dict[str, Any],
        adapted: ProtocolAdaptedRun,
        runtime_snapshot: Dict[str, Any],
        pending_client_tool_calls: List[Dict[str, Any]],
    ) -> AgentRunCheckpoint:
        record = await self.checkpoint_dao.get_by_execution_id(resource_execution_id=execution.id)
        snapshot = {
            "input_content": adapted.input_content,
            "thread_id": adapted.thread_id,
            "custom_history": [self._message_to_json(item) for item in adapted.custom_history],
            "resume_messages": [self._message_to_json(item) for item in adapted.resume_messages],
            "has_custom_history": adapted.has_custom_history,
            "resume_tool_call_ids": list(adapted.resume_tool_call_ids),
            "resume_interrupt_id": adapted.resume_interrupt_id,
        }
        adapted_snapshot, runtime_snapshot = self._compact_snapshots(
            checkpoint_kind=checkpoint_kind,
            adapted_snapshot=snapshot,
            runtime_snapshot=runtime_snapshot,
        )
        if record is None:
            record = AgentRunCheckpoint(
                resource_execution_id=execution.id,
                agent_instance_id=agent_instance.id,
                session_id=session_id,
                thread_id=thread_id,
                turn_id=turn_id,
            )
            self.db.add(record)

        record.session_id = session_id
        record.thread_id = thread_id
        record.turn_id = turn_id
        record.checkpoint_kind = checkpoint_kind
        record.run_input_payload = run_input_payload
        record.adapted_snapshot = adapted_snapshot
        record.runtime_snapshot = runtime_snapshot
        record.pending_client_tool_calls = pending_client_tool_calls
        await self.db.flush()
        return record

    async def get_checkpoint(self, *, execution_id: int) -> Optional[AgentRunCheckpoint]:
        return await self.checkpoint_dao.get_by_execution_id(resource_execution_id=execution_id)

    async def delete_checkpoint(self, *, execution_id: int) -> None:
        record = await self.get_checkpoint(execution_id=execution_id)
        if record is not None:
            await self.db.delete(record)
            await self.db.flush()

    async def append_events(
        self,
        *,
        execution: ResourceExecution,
        agent_instance: Agent,
        session_id: Optional[int],
        events: List[Dict[str, Any]],
    ) -> None:
        last_event = await self.event_dao.get_last_event(resource_execution_id=execution.id)
        sequence = 1 if last_event is None else last_event.sequence_no + 1
        for item in events:
            event = AgentRunEvent(
                resource_execution_id=execution.id,
                agent_instance_id=agent_instance.id,
                session_id=session_id,
                sequence_no=sequence,
                event_type=str(item["event_type"]),
                payload=item["payload"],
            )
            self.db.add(event)
            sequence += 1
        await self.db.flush()

    async def upsert_tool_histories(
        self,
        *,
        execution: ResourceExecution,
        agent_instance: Agent,
        session_id: Optional[int],
        turn_id: Optional[str],
        histories: List[Dict[str, Any]],
    ) -> None:
        for item in histories:
            record = await self.tool_dao.get_by_run_and_tool_call(
                resource_execution_id=execution.id,
                tool_call_id=item["tool_call_id"],
            )
            if record is None:
                record = AgentToolExecution(
                    resource_execution_id=execution.id,
                    agent_instance_id=agent_instance.id,
                    session_id=session_id,
                    turn_id=turn_id,
                    tool_call_id=item["tool_call_id"],
                    tool_name=item["tool_name"],
                )
                self.db.add(record)

            record.status = item["status"]
            record.step_index = item.get("step_index")
            record.thought = item.get("thought")
            record.arguments = item.get("arguments")
            record.result = item.get("result")
            record.error_message = item.get("error_message")
        await self.db.flush()

    async def list_events(self, *, execution_id: int, limit: int = 1000) -> List[AgentRunEventRead]:
        rows = await self.event_dao.get_list(
            where={"resource_execution_id": execution_id},
            order=["sequence_no"],
            limit=limit,
        )
        return [AgentRunEventRead.model_validate(row) for row in rows]

    async def list_tool_executions(self, *, execution_id: int) -> List[AgentToolExecutionRead]:
        rows = await self.tool_dao.get_list(
            where={"resource_execution_id": execution_id},
            order=["id"],
        )
        return [AgentToolExecutionRead.model_validate(row) for row in rows]

    def build_run_summary(
        self,
        *,
        execution: ResourceExecution,
    ) -> AgentRunSummaryRead:
        status_value = execution.status.value if hasattr(execution.status, "value") else str(execution.status)
        return AgentRunSummaryRead(
            run_id=execution.run_id,
            thread_id=execution.thread_id,
            parent_run_id=execution.parent_run_id,
            status=status_value,
            trace_id=execution.trace_id,
            error_code=execution.error_code,
            error_message=execution.error_message,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
        )

    def build_run_detail(
        self,
        *,
        execution: ResourceExecution,
        agent_instance: Agent,
        checkpoint: Optional[AgentRunCheckpoint],
        events: List[AgentRunEventRead],
        tool_executions: List[AgentToolExecutionRead],
    ) -> AgentRunDetailRead:
        summary = self.build_run_summary(execution=execution)
        return AgentRunDetailRead(
            **summary.model_dump(),
            agent_instance_uuid=agent_instance.uuid,
            agent_name=agent_instance.name,
            latest_checkpoint=AgentRunCheckpointRead.model_validate(checkpoint) if checkpoint else None,
            events=events,
            tool_executions=tool_executions,
        )
