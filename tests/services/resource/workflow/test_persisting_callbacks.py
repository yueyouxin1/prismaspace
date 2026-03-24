from types import SimpleNamespace

import pytest

from app.services.resource.workflow.persisting_callbacks import PersistingWorkflowCallbacks
from app.utils.async_generator import AsyncGeneratorManager


pytestmark = pytest.mark.asyncio


async def test_workflow_callbacks_emit_and_capture_without_db_persistence():
    generator = AsyncGeneratorManager()
    sink_payloads = []

    async def _sink(payload):
        sink_payloads.append(payload)
        return {"seq": len(sink_payloads), "payload": payload}

    callbacks = PersistingWorkflowCallbacks(
        generator_manager=generator,
        trace_id="trace-1",
        run_id="run-1",
        thread_id="thread-1",
        event_sink=_sink,
    )

    await callbacks.on_event("stream.delta", {"chunk": "A"})

    event = await generator.get()
    assert event.event == "stream.delta"
    assert event.data == {"chunk": "A", "run_id": "run-1", "thread_id": "thread-1"}
    assert event.id == "1"
    assert sink_payloads == [{"event": "stream.delta", "data": event.data}]
    assert callbacks.get_captured_events() == []


async def test_workflow_callbacks_enrich_node_state_events():
    generator = AsyncGeneratorManager()
    callbacks = PersistingWorkflowCallbacks(
        generator_manager=generator,
        trace_id="trace-1",
        run_id="run-1",
        thread_id="thread-1",
    )

    callbacks._bind_runtime_plan(
        SimpleNamespace(
            all_nodes=[SimpleNamespace(id="node-1", registry_id="LLMNode", name="LLM")],
        )
    )

    await callbacks.on_node_start(
        SimpleNamespace(
            model_dump=lambda: {"node_id": "node-1", "status": "RUNNING"},
            node_id="node-1",
        )
    )
    event = await generator.get()
    assert event.data["node"]["id"] == "node-1"
    assert callbacks.get_captured_events() == [
        {
            "event_type": "node.started",
            "payload": event.data,
        }
    ]
