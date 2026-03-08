from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.engine.model.llm import LLMMessage
from app.models.resource.agent import AgentMessageRole
from app.schemas.protocol import RunAgentInputExt, RunAgentPlatformProps
from app.services.resource.agent.ag_ui_processor import AgUiProcessor
from app.services.resource.agent.agent_service import AgentService
from app.services.resource.agent.ag_ui_normalizer import AgUiNormalizer
from app.services.resource.agent.protocol_adapter import AgUiProtocolAdapter
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
            "interruptId": "123e4567-e89b-12d3-a456-426614174000",
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
    assert processed.thread_id == "thread-1"
    assert len(processed.llm_tools) == 1

    assert any(msg.role == "system" and "[CONTEXT]" in (msg.content or "") for msg in processed.custom_history)
    assert any(msg.role == "tool" and msg.tool_call_id == "call-1" for msg in processed.resume_messages)


def test_protocol_adapter_registers_client_tools_during_adaptation():
    adapter = AgUiProtocolAdapter()
    run_input = _build_run_input()

    class _Registrar:
        def __init__(self):
            self.names = []

        def register_client_tool(self, tool_def):
            self.names.append(tool_def.function.name)

    registrar = _Registrar()
    adapted = adapter.adapt(run_input, tool_registrar=registrar)

    assert adapted.thread_id == "thread-1"
    assert len(adapted.client_tools) == 1
    assert registrar.names == ["ask_user_confirm"]


def test_protocol_adapter_extracts_resume_tool_call_ids():
    adapter = AgUiProtocolAdapter()
    run_input = _build_run_input(
        resume={
            "interruptId": "123e4567-e89b-12d3-a456-426614174000",
            "payload": {
                "toolResults": [
                    {"toolCallId": "call-1", "content": {"ok": True}},
                    {"toolCallId": "call-2", "content": {"ok": False}},
                ]
            },
        }
    )

    adapted = adapter.adapt(run_input)

    assert adapted.resume_tool_call_ids == ["call-1", "call-2"]
    assert adapted.resume_interrupt_id == "123e4567-e89b-12d3-a456-426614174000"

def test_protocol_adapter_marks_custom_history_presence():
    adapter = AgUiProtocolAdapter()
    with_history = _build_run_input()
    user_only = _build_run_input(messages=[{"id": "u1", "role": "user", "content": "hello"}])

    adapted_with_history = adapter.adapt(with_history)
    adapted_user_only = adapter.adapt(user_only)

    assert adapted_with_history.has_custom_history is True
    assert adapted_user_only.has_custom_history is False


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
    assert activity is None


def test_normalizer_preserves_assistant_and_tool_encrypted_values():
    normalizer = AgUiNormalizer()
    run_input = RunAgentInputExt.model_validate(
        {
            "threadId": "thread-1",
            "runId": "run-1",
            "state": {},
            "messages": [
                {
                    "id": "a1",
                    "role": "assistant",
                    "content": "answer",
                    "encryptedValue": "enc-assistant",
                },
                {
                    "id": "t1",
                    "role": "tool",
                    "content": "result",
                    "toolCallId": "call-1",
                    "encryptedValue": "enc-tool",
                },
            ],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
    )

    assistant = normalizer.agui_message_to_llm_message(run_input.messages[0])
    tool = normalizer.agui_message_to_llm_message(run_input.messages[1])

    assert assistant is not None
    assert assistant.encrypted_value == "enc-assistant"
    assert tool is not None
    assert tool.encrypted_value == "enc-tool"


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
                "interruptId": "123e4567-e89b-12d3-a456-426614174000",
                "payload": {"toolResults": [{"toolCallId": "call-1", "content": {"ok": True}}]},
            },
        }
    )

    processed = processor.agui_to_agent_runtime(run_input)

    assert processed.input_content == ""
    assert any(msg.role == "tool" and msg.tool_call_id == "call-1" for msg in processed.resume_messages)


def test_processor_accepts_long_thread_id():
    normalizer = AgUiNormalizer()
    processor = AgUiProcessor(normalizer)
    run_input = _build_run_input(threadId="thread-" + "x" * 120)

    processed = processor.agui_to_agent_runtime(run_input)

    assert processed.thread_id == run_input.thread_id


def test_processor_rejects_resume_payload_without_tool_results_when_no_user_input():
    normalizer = AgUiNormalizer()
    processor = AgUiProcessor(normalizer)
    run_input = RunAgentInputExt.model_validate(
        {
            "threadId": "thread-1",
            "runId": "run-1",
            "state": {},
            "messages": [{"id": "a1", "role": "assistant", "content": "waiting for approval"}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
            "resume": {
                "interruptId": "approval-1",
                "payload": {
                    "approved": True,
                    "note": "continue",
                },
            },
        }
    )

    with pytest.raises(ServiceException, match="must include at least one user message content"):
        processor.agui_to_agent_runtime(run_input)


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
                    "interruptId": "123e4567-e89b-12d3-a456-426614174000",
                    "payload": {"toolResults": [{"content": {"ok": True}}]},
                },
            }
        )


def test_event_to_payload_unknown_value_maps_to_raw_event():
    payload = AgentService._event_to_payload(object())
    assert payload["type"] == "RAW"
    assert payload["source"] == "prismaspace.agent"


def test_is_valid_uuid():
    assert AgentService._is_valid_uuid("123e4567-e89b-12d3-a456-426614174000")
    assert AgentService._is_valid_uuid("123E4567-E89B-12D3-A456-426614174000")
    assert not AgentService._is_valid_uuid("")
    assert not AgentService._is_valid_uuid("thread-1")
    assert not AgentService._is_valid_uuid("123e4567e89b12d3a456426614174000")


def test_resolve_protocol_name_defaults_and_aliases():
    run_default = _build_run_input()
    run_alias = _build_run_input(forwardedProps={"platform": {"protocol": "AGUI"}})

    assert AgentService._resolve_protocol_name(run_default) == "ag-ui"
    assert AgentService._resolve_protocol_name(run_alias) == "ag-ui"


def test_forwarded_props_platform_protocol_rejects_unknown_value():
    with pytest.raises(ValidationError, match="protocol must be 'ag-ui'"):
        _build_run_input(forwardedProps={"platform": {"protocol": "mcp"}})


def test_interrupt_id_helpers_require_canonical_run_id():
    run_id = "123e4567-e89b-12d3-a456-426614174000"

    assert AgentService.build_interrupt_id(run_id) == run_id
    assert AgentService._normalize_interrupt_id(run_id) == run_id
    assert AgentService._normalize_interrupt_id("thread-1") is None
    assert AgentService._normalize_interrupt_id("bad-value") is None


def test_build_stream_message_ids_always_generates_platform_user_message_id():
    ids = AgentService._build_stream_message_ids()

    assert ids.user_message_id != "user-msg-1"
    assert len(ids.user_message_id) == 36


def test_build_stream_message_ids_generates_platform_ids():
    ids = AgentService._build_stream_message_ids()

    assert ids.user_message_id != ids.assistant_message_id
    assert len(ids.user_message_id) == 36
    assert len(ids.assistant_message_id) == 36


def test_resolve_session_mode_defaults_and_aliases():
    run_default = _build_run_input()
    run_stateless = _build_run_input(forwardedProps={"platform": {"sessionMode": "STATELESS"}})
    run_stateful = _build_run_input(forwardedProps={"platform": {"sessionMode": "session"}})

    assert AgentService._resolve_session_mode(run_default) == "auto"
    assert AgentService._resolve_session_mode(run_stateless) == "stateless"
    assert AgentService._resolve_session_mode(run_stateful) == "stateful"


def test_forwarded_props_platform_rejects_unknown_keys():
    with pytest.raises(ValidationError):
        _build_run_input(forwardedProps={"platform": {"debug": True}})


def test_forwarded_props_outer_extensions_remain_open_and_preserved():
    run_input = _build_run_input(
        forwardedProps={
            "platform": {"sessionMode": "auto"},
            "trace": "trace-123",
            "middleware": {"debug": True},
        }
    )

    forwarded_props = run_input.forwarded_props.model_dump(by_alias=True, exclude_none=True)

    assert forwarded_props["trace"] == "trace-123"
    assert forwarded_props["middleware"] == {"debug": True}
    assert forwarded_props["platform"]["sessionMode"] == "auto"


def test_platform_agent_uuid_schema_marks_websocket_only_usage():
    schema = RunAgentPlatformProps.model_json_schema(by_alias=True)
    agent_uuid = schema["properties"]["agentUuid"]

    assert "WebSocket-only" in agent_uuid["description"]


def test_requires_persistent_session_binding_only_for_session_intent():
    run_auto = _build_run_input()
    run_with_uuid_thread = _build_run_input(threadId="123e4567-e89b-12d3-a456-426614174000")
    run_with_extra_forwarded_prop = _build_run_input(forwardedProps={"trace": "123"})
    run_stateless = _build_run_input(
        threadId="123e4567-e89b-12d3-a456-426614174000",
        forwardedProps={"platform": {"sessionMode": "stateless"}},
    )

    assert AgentService._requires_persistent_session_binding(run_auto) is False
    assert AgentService._requires_persistent_session_binding(run_with_uuid_thread) is True
    assert AgentService._requires_persistent_session_binding(run_with_extra_forwarded_prop) is False
    assert AgentService._requires_persistent_session_binding(run_stateless) is False


def test_resolve_model_context_window_with_fallback_keys():
    assert AgentService._resolve_model_context_window({"context_window": 128000}) == 128000
    assert AgentService._resolve_model_context_window({"max_context_tokens": 64000}) == 8192
    assert AgentService._resolve_model_context_window({"max_input_tokens": "32000"}) == 8192
    assert AgentService._resolve_model_context_window({}) == 8192


def test_extract_pending_tool_call_ids():
    messages = [
        SimpleNamespace(
            role=AgentMessageRole.ASSISTANT,
            tool_calls=[{"id": "call-1"}, {"id": "call-2"}],
            tool_call_id=None,
        ),
        SimpleNamespace(
            role=AgentMessageRole.TOOL,
            tool_calls=None,
            tool_call_id="call-1",
        ),
        SimpleNamespace(
            role=AgentMessageRole.ASSISTANT,
            tool_calls=[{"id": "call-3"}],
            tool_call_id=None,
        ),
    ]

    pending = AgentService._extract_pending_tool_call_ids(messages)

    assert pending == {"call-2", "call-3"}


def test_buffer_protocol_history_messages_persists_reasoning_and_tool_result():
    service = object.__new__(AgentService)
    calls = []
    session_manager = SimpleNamespace(
        session=SimpleNamespace(id=1),
        buffer_message=lambda **kwargs: calls.append(kwargs),
    )
    history = [
        LLMMessage(role="system", content="[REASONING]\nchain"),
        LLMMessage(role="tool", content='{"ok":true}', tool_call_id="call-1"),
    ]

    service._buffer_protocol_history_messages(session_manager, history)

    assert len(calls) == 2
    assert calls[0]["role"] == AgentMessageRole.REASONING
    assert calls[0]["reasoning_content"] == "chain"
    assert calls[1]["role"] == AgentMessageRole.TOOL
    assert calls[1]["tool_call_id"] == "call-1"


@pytest.mark.asyncio
async def test_enforce_pending_tool_results_requires_resume_matches():
    service = object.__new__(AgentService)
    assistant_with_tool = SimpleNamespace(
        role=AgentMessageRole.ASSISTANT,
        tool_calls=[{"id": "call-1"}],
        tool_call_id=None,
    )
    session_manager = SimpleNamespace(
        session=SimpleNamespace(id=1),
        get_recent_messages=AsyncMock(return_value=[assistant_with_tool]),
    )

    with pytest.raises(ServiceException, match="Pending client tool results are required"):
        await service._enforce_pending_tool_results(
            session_manager=session_manager,
            resume_tool_call_ids=[],
        )
    session_manager.get_recent_messages.assert_awaited_once_with(1)


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
    response = await service.sync_execute("agent-uuid", run_input, actor=SimpleNamespace())

    assert response.thread_id == "thread-1"
    assert response.run_id == "run-1"
    assert [item["type"] for item in response.events] == ["RUN_STARTED", "TEXT_MESSAGE_CONTENT", "RUN_FINISHED"]
