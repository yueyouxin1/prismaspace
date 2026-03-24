from types import SimpleNamespace

from app.engine.workflow.context import WorkflowRuntimeSnapshot
from app.models.resource.workflow.runtime import WorkflowCheckpointReason
from app.services.resource.workflow.runtime_persistence import WorkflowRuntimePersistenceService


def test_compact_checkpoint_state_only_for_execution_succeeded():
    snapshot = WorkflowRuntimeSnapshot(
        payload={"input": "hello"},
        variables={"node": {"value": "x"}},
        node_states={"node": {"status": "COMPLETED"}},
        ready_queue=["end"],
        step_index=7,
    )
    runtime_plan = SimpleNamespace(nodes=[{"id": "start"}])

    succeeded = WorkflowRuntimePersistenceService._compact_checkpoint_state(
        reason=WorkflowCheckpointReason.EXECUTION_SUCCEEDED,
        runtime_plan=runtime_plan,
        snapshot=snapshot,
    )
    failed = WorkflowRuntimePersistenceService._compact_checkpoint_state(
        reason=WorkflowCheckpointReason.EXECUTION_FAILED,
        runtime_plan=runtime_plan,
        snapshot=snapshot,
    )

    assert succeeded[0] == {"compacted": True, "reason": "execution_succeeded"}
    assert succeeded[1] == {"input": "hello"}
    assert succeeded[2] == {}
    assert succeeded[3] == {}
    assert succeeded[4] == []

    assert failed[0] is runtime_plan
    assert failed[1] == {"input": "hello"}
    assert failed[2] == {"node": {"value": "x"}}
    assert failed[3] == {"node": {"status": "COMPLETED"}}
    assert failed[4] == ["end"]
