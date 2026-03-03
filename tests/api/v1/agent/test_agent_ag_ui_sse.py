import json
from types import SimpleNamespace

import pytest

from app.api.v1.agent import agent_api
from app.schemas.protocol import AgUiRunAgentInput
from app.schemas.resource.agent.agent_schemas import AgentEvent
from app.services.exceptions import ServiceException


def _build_run_input():
    return AgUiRunAgentInput.model_validate(
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

        async def async_execute(self, instance_uuid, request, actor):
            async def _gen():
                yield AgentEvent(event="message.delta", data={"delta": "hello"})
                yield AgentEvent(event="done", data={"status": "completed", "result": {"ok": True}})

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
async def test_stream_agent_route_wraps_service_exception_to_run_error(monkeypatch):
    class _FailingService:
        def __init__(self, context):
            self.context = context

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
