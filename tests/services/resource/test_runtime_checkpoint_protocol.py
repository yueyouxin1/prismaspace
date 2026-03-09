from datetime import datetime
from types import SimpleNamespace

from app.services.resource.agent.run_persistence import AgentRunPersistenceService
from app.services.resource.workflow.runtime_persistence import WorkflowRuntimePersistenceService


def test_agent_checkpoint_builds_canonical_envelope():
    service = AgentRunPersistenceService(SimpleNamespace(db=None))
    execution = SimpleNamespace(
        run_id="run-1",
        thread_id="thread-1",
        parent_run_id=None,
        trace_id="trace-1",
    )
    checkpoint = SimpleNamespace(
        thread_id="thread-1",
        turn_id="turn-1",
        checkpoint_kind="interrupted",
        runtime_snapshot={
            "schema_version": 1,
            "phase": "interrupt",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function"}],
            "steps": [{"thought": "x"}],
            "next_iteration": 2,
        },
        pending_client_tool_calls=[{"tool_call_id": "call-1"}],
        created_at=datetime(2026, 3, 9, 12, 0, 0),
        updated_at=datetime(2026, 3, 9, 12, 0, 1),
    )

    read = service.build_checkpoint_read(execution=execution, checkpoint=checkpoint)

    assert read.canonical is not None
    assert read.canonical.resource_type == "agent"
    assert read.canonical.phase == "interrupt"
    assert read.canonical.summary.pending_client_tool_call_count == 1
    assert read.canonical.summary.next_iteration == 2


def test_workflow_checkpoint_builds_canonical_envelope():
    service = WorkflowRuntimePersistenceService(SimpleNamespace(db=None))
    execution = SimpleNamespace(
        run_id="run-2",
        thread_id="thread-2",
        parent_run_id="run-1",
        trace_id="trace-2",
    )
    checkpoint = SimpleNamespace(
        id=7,
        step_index=5,
        reason=SimpleNamespace(value="node_failed"),
        node_id="node-1",
        runtime_plan={"nodes": []},
        payload={"x": 1},
        variables={"y": 2},
        node_states={"node-1": {"status": "FAILED"}},
        ready_queue=["node-2"],
        created_at=datetime(2026, 3, 9, 12, 0, 0),
    )

    read = service.build_checkpoint_read(execution=execution, checkpoint=checkpoint)

    assert read.canonical is not None
    assert read.canonical.resource_type == "workflow"
    assert read.canonical.reason == "node_failed"
    assert read.canonical.summary.step_index == 5
    assert read.canonical.summary.ready_queue_size == 1
