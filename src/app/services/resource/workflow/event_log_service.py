from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.context import AppContext
from app.dao.resource.workflow.workflow_event_dao import WorkflowExecutionEventDao
from app.models import ResourceExecution, Workflow
from app.models.resource.workflow import WorkflowExecutionEvent, WorkflowExecutionEventType
from app.schemas.resource.workflow.workflow_schemas import WorkflowEventRead
from app.services.base_service import BaseService


class WorkflowEventLogService(BaseService):
    def __init__(self, context: AppContext):
        self.context = context
        self.db = context.db
        self.dao = WorkflowExecutionEventDao(self.db)

    async def append_event(
        self,
        *,
        execution: ResourceExecution,
        workflow_instance: Workflow,
        event_type: str,
        payload: Dict[str, Any],
    ) -> WorkflowExecutionEvent:
        last_event = await self.dao.get_last_event(resource_execution_id=execution.id)
        sequence_no = 1 if last_event is None else last_event.sequence_no + 1
        event = WorkflowExecutionEvent(
            resource_execution_id=execution.id,
            workflow_instance_id=workflow_instance.id,
            sequence_no=sequence_no,
            event_type=WorkflowExecutionEventType(event_type).value,
            payload=payload,
        )
        self.db.add(event)
        await self.db.flush()
        return event

    async def list_events(
        self,
        *,
        execution_id: int,
        limit: int = 1000,
    ) -> List[WorkflowEventRead]:
        rows = await self.dao.get_list(
            where={"resource_execution_id": execution_id},
            order=["sequence_no"],
            limit=limit,
        )
        return [WorkflowEventRead.model_validate(row) for row in rows]
