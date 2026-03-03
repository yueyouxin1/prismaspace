import json
from types import SimpleNamespace

import pytest

from app.schemas.protocol import AgUiRunAgentInput
from app.schemas.resource.agent.agent_schemas import AgentEvent
from app.services.resource.agent.ag_ui_adapter import AgUiAgentAdapter, encode_sse_data


class _DummyAgentService:
    def __init__(self, events):
        self._events = events
        self.last_request = None

    async def async_execute(self, instance_uuid, request, actor):
        self.last_request = (instance_uuid, request, actor)

        async def _gen():
            for event in self._events:
                yield event

        return SimpleNamespace(generator=_gen())


def _build_run_input(**overrides):
    payload = {
        "threadId": "thread-1",
        "runId": "run-1",
        "state": {"phase": "init"},
        "messages": [
            {"id": "m-system", "role": "system", "content": "system message"},
            {"id": "m-user", "role": "user", "content": "hello"},
        ],
        "tools": [
            {
                "name": "ask_user_confirm",
                "description": "Ask user to confirm",
                "parameters": {"type": "object", "properties": {"question": {"type": "string"}}},
            }
        ],
        "context": [{"description": "workspace", "value": "prismaspace"}],
        "forwardedProps": {"trace": "x"},
    }
    payload.update(overrides)
    return AgUiRunAgentInput.model_validate(payload)


@pytest.mark.asyncio
async def test_to_execution_request_maps_resume_and_tools():
    run_input = _build_run_input(
        resume={
            "interruptId": "int-1",
            "payload": {
                "toolResults": [
                    {
                        "toolCallId": "call-1",
                        "content": {"approved": True},
                    }
                ]
            },
        }
    )
    service = _DummyAgentService(events=[])
    adapter = AgUiAgentAdapter(service)

    request, ctx = adapter.to_execution_request(run_input)

    assert request.inputs.input_query == "hello"
    assert ctx.thread_id == "thread-1"
    assert ctx.run_id == "run-1"
    assert request.meta["ag_ui"]["tools"][0]["function"]["name"] == "ask_user_confirm"
    assert request.meta["ag_ui"]["resume"]["interruptId"] == "int-1"

    # context block + resume tool result should both be injected into history
    assert any(msg.role == "system" and "[AG-UI-CONTEXT]" in (msg.content or "") for msg in request.inputs.history)
    assert any(msg.role == "tool" and msg.tool_call_id == "call-1" for msg in request.inputs.history)


@pytest.mark.asyncio
async def test_stream_events_maps_to_ag_ui_event_schema():
    events = [
        AgentEvent(event="message.delta", data={"delta": "", "phase": "start"}),
        AgentEvent(event="reasoning.delta", data={"delta": "plan"}),
        AgentEvent(event="message.delta", data={"delta": "hello"}),
        AgentEvent(
            event="tool.started",
            data={
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "ask_user_confirm", "arguments": "{\"question\":\"ok?\"}"},
                    }
                ]
            },
        ),
        AgentEvent(event="tool.finished", data={"tool_call_id": "call-1", "output": {"approved": True}}),
        AgentEvent(event="usage", data={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}),
        AgentEvent(event="done", data={"status": "completed", "result": {"ok": True}}),
    ]
    service = _DummyAgentService(events=events)
    adapter = AgUiAgentAdapter(service)
    run_input = _build_run_input()

    emitted = []
    async for event in adapter.stream_events("agent-uuid", run_input, actor=SimpleNamespace()):
        emitted.append(event)

    event_types = [event["type"] for event in emitted]
    assert event_types[:4] == ["RUN_STARTED", "MESSAGES_SNAPSHOT", "STATE_SNAPSHOT", "STATE_DELTA"]
    assert "snapshot" in emitted[2]
    assert "state" not in emitted[2]

    assert "REASONING_START" in event_types
    assert "REASONING_MESSAGE_CONTENT" in event_types
    assert "TEXT_MESSAGE_START" in event_types
    assert "TEXT_MESSAGE_CONTENT" in event_types
    assert "TOOL_CALL_START" in event_types
    assert "TOOL_CALL_ARGS" in event_types
    assert "TOOL_CALL_END" in event_types
    assert "TOOL_CALL_RESULT" in event_types
    assert "CUSTOM" in event_types

    run_finished = next(event for event in emitted if event["type"] == "RUN_FINISHED")
    assert run_finished["outcome"] == "success"
    assert run_finished["threadId"] == "thread-1"
    assert run_finished["runId"] == "run-1"

    assert emitted[-1]["type"] == "STATE_DELTA"
    assert emitted[-1]["delta"][0]["value"] == "completed"


@pytest.mark.asyncio
async def test_stream_events_interrupt_and_error_mapping():
    interrupt_service = _DummyAgentService(
        events=[
            AgentEvent(
                event="done",
                data={
                    "status": "interrupt",
                    "interrupt": {"id": "int-1", "reason": "tool_result_required", "payload": {"tool_calls": []}},
                    "result": {"partial": True},
                },
            )
        ]
    )
    interrupt_adapter = AgUiAgentAdapter(interrupt_service)
    run_input = _build_run_input()
    interrupt_events = []
    async for event in interrupt_adapter.stream_events("agent-uuid", run_input, actor=SimpleNamespace()):
        interrupt_events.append(event)

    interrupt_finish = next(event for event in interrupt_events if event["type"] == "RUN_FINISHED")
    assert interrupt_finish["outcome"] == "interrupt"
    assert interrupt_finish["interrupt"]["id"] == "int-1"
    assert interrupt_events[-1]["delta"][0]["value"] == "interrupted"

    error_service = _DummyAgentService(
        events=[AgentEvent(event="error", data={"code": "X_ERR", "message": "boom", "retriable": True})]
    )
    error_adapter = AgUiAgentAdapter(error_service)
    error_events = []
    async for event in error_adapter.stream_events("agent-uuid", run_input, actor=SimpleNamespace()):
        error_events.append(event)

    run_error = next(event for event in error_events if event["type"] == "RUN_ERROR")
    assert run_error["code"] == "X_ERR"
    assert run_error["message"] == "boom"
    assert run_error["retriable"] is True
    assert error_events[-1]["delta"][0]["value"] == "error"


def test_encode_sse_data_format():
    encoded = encode_sse_data({"type": "RUN_STARTED", "threadId": "t1", "runId": "r1"})
    assert encoded.startswith("data: ")
    assert encoded.endswith("\n\n")
    payload = json.loads(encoded.removeprefix("data: ").strip())
    assert payload["type"] == "RUN_STARTED"
