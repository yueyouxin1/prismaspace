from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.protocol import RunAgentInputExt
from app.services.resource.agent.ag_ui_processor import AgUiProcessor
from app.services.resource.agent.agent_service import AgentService
from app.services.resource.agent.ag_ui_normalizer import AgUiNormalizer
from app.services.exceptions import ServiceException


def _build_run_input(**overrides):
    payload = {
        "threadId": "thread-1",
        "runId": "run-1",
        "state": {"phase": "init"},
        "messages": [
            {"id": "m-system", "role": "system", "content": "system message"},
            {"id": "m-user", "role": "user", "content": [{"type": "text", "text": "hello"}]},
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
    return RunAgentInputExt.model_validate(payload)


@pytest.mark.asyncio
async def test_build_ag_ui_inputs_maps_resume_multimodal_and_thread_session():
    normalizer = AgUiNormalizer()
    processor = AgUiProcessor(normalizer)
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

    processed = processor.agui_to_agent_runtime(run_input)

    assert isinstance(processed.input_content, list)
    assert processed.input_content[0]["type"] == "text"
    assert processed.input_content[0]["text"] == "hello"
    assert processed.session_uuid == "thread-1"
    assert len(processed.llm_tools) == 1

    assert not any(msg.role == "system" and "[AG-UI-CONTEXT]" in (msg.content or "") for msg in processed.history)
    assert any(msg.role == "tool" and msg.tool_call_id == "call-1" for msg in processed.history)


def test_normalizer_does_not_prefix_developer_content():
    normalizer = AgUiNormalizer()
    message = RunAgentInputExt.model_validate(
        {
            "threadId": "thread-1",
            "runId": "run-1",
            "state": {},
            "messages": [{"id": "d1", "role": "developer", "content": "Keep raw"}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
    ).messages[0]

    llm_message = normalizer.agui_message_to_llm_message(message)
    assert llm_message is not None
    assert llm_message.role == "system"
    assert llm_message.content == "Keep raw"


def test_normalizer_maps_reasoning_and_activity_to_system_context_messages():
    normalizer = AgUiNormalizer()
    run_input = RunAgentInputExt.model_validate(
        {
            "threadId": "thread-1",
            "runId": "run-1",
            "state": {},
            "messages": [
                {"id": "r1", "role": "reasoning", "content": "deliberating"},
                {"id": "a1", "role": "activity", "activityType": "PLAN", "content": {"stage": "tool"}},
            ],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
    )

    reasoning = normalizer.agui_message_to_llm_message(run_input.messages[0])
    activity = normalizer.agui_message_to_llm_message(run_input.messages[1])

    assert reasoning is not None
    assert reasoning.role == "system"
    assert "[REASONING]" in reasoning.content
    assert activity is not None
    assert activity.role == "system"
    assert "[ACTIVITY:PLAN]" in activity.content


def test_processor_raises_when_no_user_message_content():
    normalizer = AgUiNormalizer()
    processor = AgUiProcessor(normalizer)
    run_input = RunAgentInputExt.model_validate(
        {
            "threadId": "thread-1",
            "runId": "run-1",
            "state": {},
            "messages": [{"id": "s1", "role": "system", "content": "only system"}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
    )

    with pytest.raises(ServiceException, match="must include at least one user message content"):
        processor.agui_to_agent_runtime(run_input)


def test_processor_allows_resume_only_without_new_user_message():
    normalizer = AgUiNormalizer()
    processor = AgUiProcessor(normalizer)
    run_input = RunAgentInputExt.model_validate(
        {
            "threadId": "thread-1",
            "runId": "run-1",
            "state": {},
            "messages": [{"id": "a1", "role": "assistant", "content": "waiting tool result"}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
            "resume": {
                "interruptId": "int-1",
                "payload": {"toolResults": [{"toolCallId": "call-1", "content": {"ok": True}}]},
            },
        }
    )

    processed = processor.agui_to_agent_runtime(run_input)

    assert processed.input_content == ""
    assert any(msg.role == "tool" and msg.tool_call_id == "call-1" for msg in processed.history)


def test_processor_accepts_long_thread_id():
    normalizer = AgUiNormalizer()
    processor = AgUiProcessor(normalizer)
    run_input = _build_run_input(threadId="thread-" + "x" * 120)

    processed = processor.agui_to_agent_runtime(run_input)

    assert processed.session_uuid == run_input.thread_id


def test_resume_payload_requires_tool_call_id():
    with pytest.raises(ValidationError):
        RunAgentInputExt.model_validate(
            {
                "threadId": "thread-1",
                "runId": "run-1",
                "state": {},
                "messages": [{"id": "u1", "role": "user", "content": "hi"}],
                "tools": [],
                "context": [],
                "forwardedProps": {},
                "resume": {
                    "interruptId": "int-1",
                    "payload": {"toolResults": [{"content": {"ok": True}}]},
                },
            }
        )


def test_event_to_payload_unknown_value_maps_to_raw_event():
    payload = AgentService._event_to_payload(object())
    assert payload["type"] == "RAW"
    assert payload["source"] == "prismaspace.agent"


def test_resolve_session_uuid_hashes_long_thread_id():
    short = AgentService._resolve_session_uuid("thread-1", actor_id=1, instance_id=1)
    long_thread = "thread-" + "x" * 128
    long_first = AgentService._resolve_session_uuid(long_thread, actor_id=1, instance_id=1)
    long_second = AgentService._resolve_session_uuid(long_thread, actor_id=1, instance_id=1)
    long_other_scope = AgentService._resolve_session_uuid(long_thread, actor_id=2, instance_id=1)

    assert short == "thread-1"
    assert len(long_first) == 36
    assert long_first == long_second
    assert long_first != long_other_scope


@pytest.mark.asyncio
async def test_execute_non_stream_returns_event_list():
    service = object.__new__(AgentService)
    run_input = _build_run_input()

    async def _fake_async_execute(instance_uuid, run_input, actor, runtime_workspace=None):
        async def _gen():
            yield {"type": "RUN_STARTED", "threadId": run_input.thread_id, "runId": run_input.run_id}
            yield {"type": "TEXT_MESSAGE_CONTENT", "messageId": "assistant-run-1", "delta": "hello"}
            yield {"type": "RUN_FINISHED", "threadId": run_input.thread_id, "runId": run_input.run_id, "outcome": "success"}

        return SimpleNamespace(generator=_gen())

    service.async_execute = _fake_async_execute
    response = await service.execute("agent-uuid", run_input, actor=SimpleNamespace())

    assert response.thread_id == "thread-1"
    assert response.run_id == "run-1"
    assert [item["type"] for item in response.events] == ["RUN_STARTED", "TEXT_MESSAGE_CONTENT", "RUN_FINISHED"]
