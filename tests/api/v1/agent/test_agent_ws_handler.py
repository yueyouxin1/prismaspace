import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocketDisconnect

from app.api.v1.agent.ws_handler import AgentSessionHandler
from app.schemas.protocol import RunAgentInputExt


def _build_run_input(*, forwarded_props):
    return RunAgentInputExt.model_validate(
        {
            "threadId": "thread-x",
            "runId": "run-x",
            "state": {},
            "messages": [{"id": "u1", "role": "user", "content": "hello"}],
            "tools": [],
            "context": [],
            "forwardedProps": forwarded_props,
        }
    )


def test_extract_agent_uuid_reads_platform_websocket_contract():
    run_input = _build_run_input(forwarded_props={"platform": {"agentUuid": "agent-1"}})

    assert AgentSessionHandler._extract_agent_uuid(run_input) == "agent-1"


def test_extract_agent_uuid_returns_none_without_platform_payload():
    run_input = _build_run_input(forwarded_props={"trace": "123"})

    assert AgentSessionHandler._extract_agent_uuid(run_input) is None


@pytest.mark.asyncio
async def test_missing_agent_uuid_error_mentions_websocket_only_platform_field():
    handler = AgentSessionHandler(SimpleNamespace(), SimpleNamespace(user=SimpleNamespace(uuid="user-1")))
    events = []

    async def _capture(payload):
        events.append(payload.model_dump(mode="json", by_alias=True, exclude_none=True))

    handler._send_event = _capture

    await handler._send_run_error(
        run_id="run-x",
        thread_id="thread-x",
        code="AG_UI_MISSING_AGENT_UUID",
        message="Missing websocket-only agent uuid in forwardedProps.platform.agentUuid",
    )

    assert events[0]["code"] == "AG_UI_MISSING_AGENT_UUID"
    assert events[0]["message"] == "Missing websocket-only agent uuid in forwardedProps.platform.agentUuid"


@pytest.mark.asyncio
async def test_websocket_disconnect_does_not_cancel_background_run(monkeypatch):
    websocket = SimpleNamespace(
        accept=AsyncMock(),
        receive_text=AsyncMock(side_effect=WebSocketDisconnect()),
    )
    handler = AgentSessionHandler(websocket, SimpleNamespace(user=SimpleNamespace(uuid="user-1")))
    handler._cancel_current_task = AsyncMock()

    await handler.run()

    handler._cancel_current_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_websocket_auto_attaches_active_run_live_events(monkeypatch):
    websocket = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(redis_service=None, vector_manager=None, arq_pool=None)),
        send_text=AsyncMock(),
    )

    class _FakeSessionContext:
        async def __aenter__(self):
            return SimpleNamespace()

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    class _FakeAgentService:
        def __init__(self, context):
            self.context = context

        async def get_active_run(self, agent_uuid, actor, thread_id):
            return {"run_id": "run-live-1", "thread_id": thread_id, "status": "running"}

        async def stream_live_run_events(self, run_id, after_seq=0):
            for payload in (
                {"type": "RUN_STARTED", "runId": run_id},
                {"type": "TEXT_MESSAGE_CONTENT", "delta": "hello"},
                {"type": "RUN_FINISHED", "runId": run_id},
            ):
                yield {"seq": after_seq + 1, "payload": payload}

        async def async_execute(self, *args, **kwargs):
            raise AssertionError("async_execute should not run when active run exists")

    monkeypatch.setattr("app.api.v1.agent.ws_handler.SessionLocal", _FakeSessionContext)
    monkeypatch.setattr("app.api.v1.agent.ws_handler.AgentService", _FakeAgentService)
    monkeypatch.setattr("app.api.v1.agent.ws_handler.AppContext", lambda **kwargs: SimpleNamespace(**kwargs))

    handler = AgentSessionHandler(websocket, SimpleNamespace(user=SimpleNamespace(uuid="user-1")))
    handler.auth_context = SimpleNamespace(user=SimpleNamespace(uuid="user-1"))

    run_input = _build_run_input(forwarded_props={"platform": {"agentUuid": "agent-1"}})
    await handler._run_chat_stream("agent-1", run_input)

    payloads = [json.loads(call.args[0]) for call in websocket.send_text.await_args_list]
    assert [item["type"] for item in payloads] == [
        "RUN_STARTED",
        "TEXT_MESSAGE_CONTENT",
        "RUN_FINISHED",
    ]
