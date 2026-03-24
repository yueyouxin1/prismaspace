from __future__ import annotations

import logging
from typing import List, Optional

from pydantic import ValidationError
from sqlalchemy import desc

from app.core.context import AppContext
from app.models import ResourceExecution, Workflow
from app.models.resource.workflow import WorkflowExecutionCheckpoint, WorkflowExecutionEvent
from app.dao.resource.workflow.workflow_event_dao import WorkflowExecutionEventDao
from app.schemas.resource.workflow.workflow_schemas import (
    WorkflowCheckpointRead,
    WorkflowEventRead,
    WorkflowInterruptRead,
    WorkflowRunNodeRead,
    WorkflowRunRead,
    WorkflowRunSummaryRead,
)
from app.services.base_service import BaseService
from app.services.resource.workflow.runtime_persistence import WorkflowRuntimePersistenceService

logger = logging.getLogger(__name__)


class WorkflowRunPersistenceService(BaseService):
    """
    Durable workflow run event persistence facade.
    Mirrors the Agent service layout so upper layers depend on a run-level
    persistence API instead of the raw event log service.
    """

    def __init__(self, context: AppContext):
        self.context = context
        self.db = context.db
        self.event_dao = WorkflowExecutionEventDao(self.db)
        self.runtime_persistence = WorkflowRuntimePersistenceService(context)

    async def append_events(
        self,
        *,
        execution: ResourceExecution,
        workflow_instance: Workflow,
        events: List[dict],
    ) -> None:
        await self.append_events_for_ids(
            execution_id=execution.id,
            workflow_instance_id=workflow_instance.id,
            events=events,
        )

    async def append_events_for_ids(
        self,
        *,
        execution_id: int,
        workflow_instance_id: int,
        events: List[dict],
    ) -> None:
        if not events:
            return

        last_event = await self.event_dao.get_last_event(resource_execution_id=execution_id)
        sequence = 1 if last_event is None else last_event.sequence_no + 1

        for item in events:
            event = WorkflowExecutionEvent(
                resource_execution_id=execution_id,
                workflow_instance_id=workflow_instance_id,
                sequence_no=sequence,
                event_type=str(item["event_type"]),
                payload=item["payload"],
            )
            self.db.add(event)
            sequence += 1

        await self.db.flush()

    async def list_events(
        self,
        *,
        execution_id: int,
        limit: int = 1000,
    ) -> List[WorkflowEventRead]:
        rows = await self.event_dao.get_list(
            where={"resource_execution_id": execution_id},
            order=["sequence_no"],
            limit=limit,
        )
        return [WorkflowEventRead.model_validate(row) for row in rows]

    async def list_events_after_sequence(
        self,
        *,
        execution_id: int,
        after_sequence_no: int,
        limit: int = 1000,
    ) -> List[WorkflowEventRead]:
        rows = await self.event_dao.get_list(
            where=[
                self.event_dao.model.resource_execution_id == execution_id,
                self.event_dao.model.sequence_no > after_sequence_no,
            ],
            order=["sequence_no"],
            limit=limit,
        )
        return [WorkflowEventRead.model_validate(row) for row in rows]

    async def get_latest_event(
        self,
        *,
        execution_id: int,
        event_type: Optional[str] = None,
    ) -> Optional[WorkflowEventRead]:
        where = {"resource_execution_id": execution_id}
        if event_type is not None:
            where["event_type"] = event_type
        row = await self.event_dao.get_one(
            where=where,
            order=[desc(self.event_dao.model.sequence_no)],
        )
        if row is None:
            return None
        return WorkflowEventRead.model_validate(row)

    async def get_latest_interrupt(
        self,
        *,
        execution_id: int,
    ) -> Optional[WorkflowInterruptRead]:
        latest_interrupt = await self.get_latest_event(
            execution_id=execution_id,
            event_type="run.interrupted",
        )
        if latest_interrupt is None:
            latest_interrupt = await self.get_latest_event(
                execution_id=execution_id,
                event_type="interrupt",
            )
        if latest_interrupt is None or not isinstance(latest_interrupt.payload, dict):
            return None

        interrupt_payload = latest_interrupt.payload.get("interrupt")
        if not isinstance(interrupt_payload, dict):
            return None

        try:
            return WorkflowInterruptRead.model_validate(interrupt_payload)
        except ValidationError:
            logger.warning(
                "Invalid persisted workflow interrupt payload for execution %s",
                execution_id,
                exc_info=True,
            )
            return None

    async def get_latest_checkpoint(
        self,
        *,
        execution_id: int,
    ) -> Optional[WorkflowExecutionCheckpoint]:
        return await self.runtime_persistence.get_latest_checkpoint(execution_id=execution_id)

    def build_resume_snapshot(
        self,
        *,
        checkpoint: WorkflowExecutionCheckpoint,
        runtime_plan,
    ):
        return self.runtime_persistence.build_resume_snapshot(
            checkpoint=checkpoint,
            runtime_plan=runtime_plan,
        )

    async def list_node_executions(
        self,
        *,
        execution_id: int,
    ) -> List[WorkflowRunNodeRead]:
        rows = await self.runtime_persistence.node_execution_dao.get_list(
            where={"resource_execution_id": execution_id},
            order=["id"],
        )
        return [WorkflowRunNodeRead.model_validate(item) for item in rows]

    def build_checkpoint_read(
        self,
        *,
        execution: ResourceExecution,
        checkpoint: WorkflowExecutionCheckpoint,
    ) -> WorkflowCheckpointRead:
        return self.runtime_persistence.build_checkpoint_read(
            execution=execution,
            checkpoint=checkpoint,
        )

    def build_run_summary(
        self,
        *,
        execution: ResourceExecution,
        latest_checkpoint: Optional[WorkflowExecutionCheckpoint] = None,
    ) -> WorkflowRunSummaryRead:
        status_value = execution.status.value if hasattr(execution.status, "value") else str(execution.status)
        return WorkflowRunSummaryRead(
            run_id=execution.run_id,
            thread_id=execution.thread_id,
            parent_run_id=execution.parent_run_id,
            status=status_value,
            trace_id=execution.trace_id,
            error_code=execution.error_code,
            error_message=execution.error_message,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            latest_checkpoint=(
                self.build_checkpoint_read(execution=execution, checkpoint=latest_checkpoint)
                if latest_checkpoint
                else None
            ),
        )

    def build_run_detail(
        self,
        *,
        execution: ResourceExecution,
        workflow_instance: Workflow,
        latest_checkpoint: Optional[WorkflowExecutionCheckpoint],
        node_executions: List[WorkflowRunNodeRead],
        can_resume: bool,
        interrupt: Optional[WorkflowInterruptRead],
    ) -> WorkflowRunRead:
        summary = self.build_run_summary(
            execution=execution,
            latest_checkpoint=latest_checkpoint,
        )
        return WorkflowRunRead(
            **summary.model_dump(),
            workflow_instance_uuid=workflow_instance.uuid,
            workflow_name=workflow_instance.name,
            node_executions=node_executions,
            can_resume=can_resume,
            interrupt=interrupt,
        )
