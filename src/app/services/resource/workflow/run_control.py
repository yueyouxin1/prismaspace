from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Dict

from app.services.exceptions import NotFoundError
from app.services.resource.workflow.runtime_persistence import WorkflowDurableRuntimeObserver
from app.services.resource.workflow.runtime_registry import WorkflowTaskRegistry


class WorkflowRunControlService:
    """
    负责 workflow run cancel/control 面。
    """

    def __init__(self, workflow_service):
        self.workflow_service = workflow_service

    async def cancel_run(self, run_id: str) -> Dict[str, Any]:
        service = self.workflow_service
        execution = await service.execution_ledger_service.get_by_run_id(run_id)
        if execution is None:
            raise NotFoundError("Workflow run not found.")

        workflow_stub = await service.dao.get_by_pk(execution.resource_instance_id)
        if workflow_stub is None:
            raise NotFoundError("Workflow instance not found.")
        workflow_instance = await service.get_by_uuid(workflow_stub.uuid)
        if workflow_instance is None:
            raise NotFoundError("Workflow instance not found.")

        await service._check_execute_perm(workflow_instance)

        await service.context.redis_service.set_json(
            WorkflowDurableRuntimeObserver.cancel_signal_key(run_id),
            {"requested_at": datetime.now(UTC).replace(tzinfo=None).isoformat()},
            expire=WorkflowDurableRuntimeObserver.CANCEL_SIGNAL_TTL,
        )
        local_cancelled = WorkflowTaskRegistry.cancel(run_id)
        return {
            "run_id": run_id,
            "accepted": True,
            "local_cancelled": local_cancelled,
        }
