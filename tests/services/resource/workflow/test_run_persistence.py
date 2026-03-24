from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services.resource.workflow.run_persistence import WorkflowRunPersistenceService


pytestmark = pytest.mark.asyncio


def test_build_run_summary_and_detail_uses_checkpoint_and_node_views():
    service = WorkflowRunPersistenceService.__new__(WorkflowRunPersistenceService)
    service.build_checkpoint_read = lambda *, execution, checkpoint: {
        "id": checkpoint.id,
        "step_index": checkpoint.step_index,
        "reason": checkpoint.reason,
        "node_id": checkpoint.node_id,
        "created_at": checkpoint.created_at,
        "canonical": None,
    }

    execution = SimpleNamespace(
        run_id="run-1",
        thread_id="thread-1",
        parent_run_id=None,
        status="succeeded",
        trace_id="trace-1",
        error_code=None,
        error_message=None,
        started_at=None,
        finished_at=None,
    )
    checkpoint = SimpleNamespace(
        id=7,
        step_index=3,
        reason="node_completed",
        node_id="node-1",
        created_at=datetime.now(UTC),
    )
    node_executions = [
        {
            "node_id": "node-1",
            "node_name": "Node 1",
            "node_type": "LLMNode",
            "attempt": 1,
            "status": "COMPLETED",
            "input": None,
            "result": None,
            "error_message": None,
            "activated_port": "0",
            "executed_time": 0.0,
            "started_at": None,
            "finished_at": None,
        }
    ]
    workflow_instance = SimpleNamespace(uuid="wf-1", name="Workflow 1")

    summary = service.build_run_summary(execution=execution, latest_checkpoint=checkpoint)
    detail = service.build_run_detail(
        execution=execution,
        workflow_instance=workflow_instance,
        latest_checkpoint=checkpoint,
        node_executions=node_executions,
        can_resume=False,
        interrupt=None,
    )

    assert summary.run_id == "run-1"
    assert summary.latest_checkpoint.id == 7
    assert summary.latest_checkpoint.reason == "node_completed"
    assert detail.workflow_instance_uuid == "wf-1"
    assert detail.workflow_name == "Workflow 1"
    assert detail.node_executions[0].node_id == "node-1"
