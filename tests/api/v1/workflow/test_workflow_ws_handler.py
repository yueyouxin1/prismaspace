import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1.workflow.ws_handler import WorkflowSessionHandler
from app.schemas.protocol import (
    WorkflowRuntimeActiveRunResolveMessage,
    WorkflowRuntimeRunAttachMessage,
    WorkflowRuntimeRunCancelMessage,
    WorkflowRuntimeRunResumeMessage,
    WorkflowRuntimeUiEventAbortMessage,
)
from app.services.resource.workflow.protocol_adapter import WorkflowRuntimeProtocolAdapter


@pytest.mark.asyncio
async def test_run_cancel_requests_runtime_cancel_without_detaching_current_stream():
    handler = WorkflowSessionHandler(SimpleNamespace(), SimpleNamespace(user=SimpleNamespace(uuid="user-1")))
    handler._request_run_cancel = AsyncMock()
    handler.current_run_id = "run-live"
    handler.current_detach = lambda: (_ for _ in ()).throw(AssertionError("detach should not be called on explicit cancel"))
    handler.current_task = asyncio.create_task(asyncio.Event().wait())

    try:
        await handler._dispatch_runtime_message(
            WorkflowRuntimeRunCancelMessage.model_validate({"runId": "run-live"})
        )
        handler._request_run_cancel.assert_awaited_once_with("run-live")
        assert handler.current_task is not None
        assert not handler.current_task.done()
    finally:
        if handler.current_task:
            handler.current_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await handler.current_task


@pytest.mark.asyncio
async def test_run_attach_detaches_current_observer_without_cancelling_run():
    handler = WorkflowSessionHandler(SimpleNamespace(), SimpleNamespace(user=SimpleNamespace(uuid="user-1")))
    detached = {"value": False}

    async def _fake_attach(run_id: str, *, after_seq: int = 0, protocol: str | None = None):
        assert run_id == "run-next"
        assert after_seq == 3
        assert protocol == "wrp"

    previous_task = asyncio.create_task(asyncio.Event().wait())
    handler.current_task = previous_task
    handler.current_detach = lambda: detached.__setitem__("value", True)
    handler._attach_live_run = AsyncMock(side_effect=_fake_attach)

    await handler._dispatch_runtime_message(
        WorkflowRuntimeRunAttachMessage.model_validate({"runId": "run-next", "afterSeq": 3})
    )

    await asyncio.sleep(0)
    assert detached["value"] is True
    assert previous_task.cancelled() is True
    assert handler.current_task is not None
    await handler.current_task
    handler._attach_live_run.assert_awaited_once_with("run-next", after_seq=3, protocol="wrp")


@pytest.mark.asyncio
async def test_run_resume_message_builds_structured_resume_request():
    handler = WorkflowSessionHandler(SimpleNamespace(), SimpleNamespace(user=SimpleNamespace(uuid="user-1")))
    captured = {}

    async def _fake_run_workflow_stream(*, instance_uuid, request, request_id):
        captured["instance_uuid"] = instance_uuid
        captured["request"] = request
        captured["request_id"] = request_id

    handler._run_workflow_stream = AsyncMock(side_effect=_fake_run_workflow_stream)

    await handler._dispatch_runtime_message(
        WorkflowRuntimeRunResumeMessage.model_validate(
            {
                "instanceUuid": "workflow-1",
                "runId": "run-parent",
                "resume": {"output": {"approved": True}},
                "requestId": "req-1",
            }
        )
    )

    assert handler.current_task is not None
    await handler.current_task
    assert captured["instance_uuid"] == "workflow-1"
    assert captured["request_id"] == "req-1"
    assert captured["request"].resume_from_run_id == "run-parent"
    assert captured["request"].resume is not None
    assert captured["request"].resume.output == {"approved": True}


@pytest.mark.asyncio
async def test_attach_live_run_emits_protocol_handshake_events(monkeypatch):
    websocket = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(redis_service=None, vector_manager=None, arq_pool=None)),
        send_text=AsyncMock(),
    )

    class _FakeSessionContext:
        async def __aenter__(self):
            return SimpleNamespace()

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    class _FakeWorkflowService:
        def __init__(self, context):
            self.context = context

        async def get_run(self, run_id):
            return SimpleNamespace(
                run_id=run_id,
                thread_id="thread-1",
                trace_id="trace-1",
                parent_run_id=None,
                workflow_instance_uuid="workflow-1",
            )

        async def get_by_uuid(self, instance_uuid):
            return SimpleNamespace(
                graph={
                    "nodes": [
                        {
                            "id": "node-1",
                            "data": {"registryId": "Start", "name": "Start"},
                        }
                    ]
                }
            )

        async def stream_live_run_events(self, run_id, after_seq=0):
            yield {
                "seq": after_seq + 1,
                "payload": {"event": "node_start", "data": {"node_id": "node-1"}},
            }

    monkeypatch.setattr("app.api.v1.workflow.ws_handler.SessionLocal", _FakeSessionContext)
    monkeypatch.setattr("app.api.v1.workflow.ws_handler.WorkflowService", _FakeWorkflowService)
    monkeypatch.setattr("app.api.v1.workflow.ws_handler.AppContext", lambda **kwargs: SimpleNamespace(**kwargs))

    handler = WorkflowSessionHandler(websocket, SimpleNamespace(user=SimpleNamespace(uuid="user-1")))
    handler.auth_context = SimpleNamespace(user=SimpleNamespace(uuid="user-1"))

    await handler._attach_live_run("run-live", after_seq=4)

    payloads = [json.loads(call.args[0]) for call in websocket.send_text.await_args_list]
    assert [item["type"] for item in payloads[:3]] == [
        "session.ready",
        "run.attached",
        "node.started",
    ]
    assert payloads[1]["payload"]["afterSeq"] == 4
    assert payloads[2]["node"]["id"] == "node-1"


def test_protocol_adapter_maps_cancelled_finish_to_run_cancelled():
    envelope = WorkflowRuntimeProtocolAdapter().build_envelope(
        event_type="finish",
        payload={"outcome": "cancelled"},
        run_id="run-1",
        thread_id="thread-1",
        trace_id="trace-1",
        parent_run_id=None,
    )

    assert envelope.type == "run.cancelled"


@pytest.mark.asyncio
async def test_active_run_resolve_is_rejected_for_general_workflow_surface():
    websocket = SimpleNamespace(send_text=AsyncMock())
    handler = WorkflowSessionHandler(websocket, SimpleNamespace(user=SimpleNamespace(uuid="user-1")))

    await handler._dispatch_runtime_message(
        WorkflowRuntimeActiveRunResolveMessage.model_validate(
            {
                "scope": {"kind": "chat-thread", "id": "thread-1"},
                "profile": "chat-thread",
            }
        )
    )

    payload = json.loads(websocket.send_text.await_args.args[0])
    assert payload["type"] == "run.failed"
    assert "reserved for scoped interactive workflow profiles" in payload["payload"]["error"]


@pytest.mark.asyncio
async def test_ui_event_abort_returns_not_enabled_protocol_error():
    websocket = SimpleNamespace(send_text=AsyncMock())
    handler = WorkflowSessionHandler(websocket, SimpleNamespace(user=SimpleNamespace(uuid="user-1")))

    await handler._dispatch_runtime_message(
        WorkflowRuntimeUiEventAbortMessage.model_validate(
            {
                "runId": "run-1",
                "interactionId": "interaction-1",
            }
        )
    )

    payload = json.loads(websocket.send_text.await_args.args[0])
    assert payload["type"] == "run.failed"
    assert "ui.event.abort is not enabled" in payload["payload"]["error"]


@pytest.mark.asyncio
async def test_unsupported_protocol_returns_runtime_error():
    websocket = SimpleNamespace(send_text=AsyncMock())
    handler = WorkflowSessionHandler(websocket, SimpleNamespace(user=SimpleNamespace(uuid="user-1")))

    await handler._dispatch_runtime_message(
        WorkflowRuntimeRunAttachMessage.model_validate(
            {
                "protocol": "chatflow-ag-ui",
                "runId": "run-1",
            }
        )
    )

    payload = json.loads(websocket.send_text.await_args.args[0])
    assert payload["type"] == "run.failed"
    assert "reserved but not implemented yet" in payload["payload"]["error"]
