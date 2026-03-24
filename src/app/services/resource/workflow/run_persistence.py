from __future__ import annotations

from typing import List, Optional

from app.core.context import AppContext
from app.models import ResourceExecution, Workflow
from app.schemas.resource.workflow.workflow_schemas import WorkflowEventRead
from app.services.base_service import BaseService
from app.services.resource.workflow.event_log_service import WorkflowEventLogService


class WorkflowRunPersistenceService(BaseService):
    """
    Durable workflow run event persistence facade.
    Mirrors the Agent service layout so upper layers depend on a run-level
    persistence API instead of the raw event log service.
    """

    def __init__(self, context: AppContext):
        self.context = context
        self.event_log_service = WorkflowEventLogService(context)

    async def append_events(
        self,
        *,
        execution: ResourceExecution,
        workflow_instance: Workflow,
        events: List[dict],
    ) -> None:
        await self.event_log_service.append_events(
            execution=execution,
            workflow_instance=workflow_instance,
            events=events,
        )

    async def list_events(
        self,
        *,
        execution_id: int,
        limit: int = 1000,
    ) -> List[WorkflowEventRead]:
        return await self.event_log_service.list_events(
            execution_id=execution_id,
            limit=limit,
        )

    async def list_events_after_sequence(
        self,
        *,
        execution_id: int,
        after_sequence_no: int,
        limit: int = 1000,
    ) -> List[WorkflowEventRead]:
        return await self.event_log_service.list_events_after_sequence(
            execution_id=execution_id,
            after_sequence_no=after_sequence_no,
            limit=limit,
        )

    async def get_latest_event(
        self,
        *,
        execution_id: int,
        event_type: Optional[str] = None,
    ) -> Optional[WorkflowEventRead]:
        return await self.event_log_service.get_latest_event(
            execution_id=execution_id,
            event_type=event_type,
        )
