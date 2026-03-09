import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1.agent import agent_api
from app.schemas.protocol import RunAgentInputExt
from app.services.exceptions import ServiceException


def _build_run_input():
    return RunAgentInputExt.model_validate(
        {
            "threadId": "thread-x",
            "runId": "run-x",
            "state": {},
            "messages": [{"id": "u1", "role": "user", "content": "hello"}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
    )


async def _collect_sse_response_chunks(streaming_response):
    chunks = []
    async for chunk in streaming_response.body_iterator:
        chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
    return "".join(chunks)


@pytest.mark.asyncio
async def test_stream_agent_route_outputs_ag_ui_sse(monkeypatch):
    class _FakeAgentService:
        def __init__(self, context):
            self.context = context

        async def get_active_run(self, instance_uuid, actor, thread_id):
            return None

        async def async_execute(self, instance_uuid, request, actor):
            async def _gen():
                yield {"type": "RUN_STARTED", "threadId": "thread-x", "runId": "run-x"}
                yield {"type": "TEXT_MESSAGE_START", "messageId": "assistant-run-x", "role": "assistant"}
                yield {"type": "TEXT_MESSAGE_CONTENT", "messageId": "assistant-run-x", "delta": "hello"}
                yield {"type": "TEXT_MESSAGE_END", "messageId": "assistant-run-x"}
                yield {"type": "RUN_FINISHED", "threadId": "thread-x", "runId": "run-x", "outcome": "success", "result": {"ok": True}}

            return SimpleNamespace(generator=_gen())

    monkeypatch.setattr(agent_api, "AgentService", _FakeAgentService)

    request = _build_run_input()
    context = SimpleNamespace(actor=SimpleNamespace())
    response = await agent_api.stream_agent("agent-1", request, context)

    body = await _collect_sse_response_chunks(response)
    payloads = [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    types = [payload["type"] for payload in payloads]

    assert types[0] == "RUN_STARTED"
    assert "TEXT_MESSAGE_CONTENT" in types
    assert "RUN_FINISHED" in types


@pytest.mark.asyncio
async def test_execute_agent_route_outputs_ag_ui_events(monkeypatch):
    class _FakeAgentService:
        def __init__(self, context):
            self.context = context

        async def sync_execute(self, instance_uuid, run_input, actor):
            return SimpleNamespace(
                thread_id=run_input.thread_id,
                run_id=run_input.run_id,
                events=[
                    {"type": "RUN_STARTED", "threadId": run_input.thread_id, "runId": run_input.run_id},
                    {"type": "RUN_FINISHED", "threadId": run_input.thread_id, "runId": run_input.run_id, "outcome": "success"},
                ],
            )

    monkeypatch.setattr(agent_api, "AgentService", _FakeAgentService)

    request = _build_run_input()
    context = SimpleNamespace(actor=SimpleNamespace())
    response = await agent_api.execute_agent("agent-1", request, context)

    assert response.data.thread_id == "thread-x"
    assert response.data.run_id == "run-x"
    assert [item["type"] for item in response.data.events] == ["RUN_STARTED", "RUN_FINISHED"]


@pytest.mark.asyncio
async def test_stream_agent_route_wraps_service_exception_to_run_error(monkeypatch):
    class _FailingService:
        def __init__(self, context):
            self.context = context

        async def get_active_run(self, instance_uuid, actor, thread_id):
            return None

        async def async_execute(self, instance_uuid, request, actor):
            raise ServiceException("boom")

    monkeypatch.setattr(agent_api, "AgentService", _FailingService)

    request = _build_run_input()
    context = SimpleNamespace(actor=SimpleNamespace())
    response = await agent_api.stream_agent("agent-1", request, context)

    body = await _collect_sse_response_chunks(response)
    payloads = [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]

    run_error = next(payload for payload in payloads if payload["type"] == "RUN_ERROR")
    assert run_error["code"] == "AGENT_SERVICE_ERROR"
    assert run_error["runId"] == "run-x"


@pytest.mark.asyncio
async def test_stream_agent_disconnect_does_not_cancel_background_run(monkeypatch):
    cancelled = {"value": False}

    class _FakeAgentService:
        def __init__(self, context):
            self.context = context

        async def get_active_run(self, instance_uuid, actor, thread_id):
            return None

        async def async_execute(self, instance_uuid, request, actor):
            async def _gen():
                yield {"type": "RUN_STARTED", "threadId": "thread-x", "runId": "run-x"}
                await asyncio.sleep(1)

            task = asyncio.create_task(asyncio.sleep(10))

            def _cancel():
                cancelled["value"] = True
                task.cancel()

            return SimpleNamespace(generator=_gen(), cancel=_cancel, task=task, thread_id="thread-x", run_id="run-x")

    monkeypatch.setattr(agent_api, "AgentService", _FakeAgentService)

    request = _build_run_input()
    context = SimpleNamespace(actor=SimpleNamespace())
    response = await agent_api.stream_agent("agent-1", request, context)

    first_chunk = await response.body_iterator.__anext__()
    assert "RUN_STARTED" in first_chunk
    await response.body_iterator.aclose()

    assert cancelled["value"] is False


@pytest.mark.asyncio
async def test_stream_agent_auto_attaches_active_run_and_replays_live_events(monkeypatch):
    class _FakeAgentService:
        def __init__(self, context):
            self.context = context

        async def get_active_run(self, instance_uuid, actor, thread_id):
            return {"run_id": "run-live-1", "thread_id": thread_id, "status": "running"}

        async def stream_live_run_events(self, run_id, after_seq=0):
            for payload in (
                {"type": "RUN_STARTED", "threadId": "thread-x", "runId": run_id},
                {"type": "TEXT_MESSAGE_CONTENT", "messageId": "assistant-run-x", "delta": "hello"},
                {"type": "RUN_FINISHED", "threadId": "thread-x", "runId": run_id, "outcome": "success"},
            ):
                yield {"seq": after_seq + 1, "payload": payload}

        async def async_execute(self, instance_uuid, request, actor):
            raise AssertionError("async_execute should not be called when an active run exists")

    monkeypatch.setattr(agent_api, "AgentService", _FakeAgentService)

    request = _build_run_input()
    context = SimpleNamespace(actor=SimpleNamespace())
    response = await agent_api.stream_agent("agent-1", request, context)

    body = await _collect_sse_response_chunks(response)
    payloads = [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    assert [payload["type"] for payload in payloads] == [
        "RUN_STARTED",
        "TEXT_MESSAGE_CONTENT",
        "RUN_FINISHED",
    ]
