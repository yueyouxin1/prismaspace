import pytest

from app.engine.agent import AgentResult, AgentClientToolCall, AgentStep
from app.engine.model.llm import LLMMessage, LLMToolCall, LLMToolCallChunk, LLMUsage
from app.schemas.protocol import RunAgentInputExt
from app.services.common.llm_capability_provider import UsageAccumulator
from app.services.resource.agent.agent_service import PersistingAgentCallbacks
from app.utils.async_generator import AsyncGeneratorManager


class _FakeSession:
    uuid = "session-1"


class _FakeSessionManager:
    def __init__(self):
        self.session = _FakeSession()
        self.buffered = []

    def buffer_message(self, role, content=None, text_content=None, content_parts=None, reasoning_content=None, tool_calls=None, tool_call_id=None, token_count=0, meta=None, **kwargs):
        self.buffered.append(
            {
                "role": role,
                "content": text_content if text_content is not None else content,
                "text_content": text_content,
                "content_parts": content_parts,
                "reasoning_content": reasoning_content,
                "tool_calls": tool_calls,
                "tool_call_id": tool_call_id,
                "token_count": token_count,
                "meta": meta,
            }
        )


def _build_run_input():
    return RunAgentInputExt.model_validate(
        {
            "threadId": "thread-1",
            "runId": "run-1",
            "state": {},
            "messages": [{"id": "u1", "role": "user", "content": "hi"}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
    )


@pytest.mark.asyncio
async def test_reasoning_plaintext_is_persisted_in_assistant_meta():
    session_manager = _FakeSessionManager()
    callbacks = PersistingAgentCallbacks(
        usage_accumulator=UsageAccumulator(),
        generator_manager=AsyncGeneratorManager(),
        session_manager=session_manager,
        trace_id="trace-1",
    )

    await callbacks.on_agent_finish(
        AgentResult(
            message=LLMMessage(role="assistant", content="final answer"),
            steps=[],
            usage=LLMUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            reasoning_content="first second",
            outcome="completed",
        )
    )

    assert len(session_manager.buffered) == 1
    persisted = session_manager.buffered[0]
    assert persisted["content"] == "final answer"
    assert persisted["reasoning_content"] == "first second"


@pytest.mark.asyncio
async def test_interrupt_event_uses_typed_tool_calls_payload():
    generator = AsyncGeneratorManager()
    callbacks = PersistingAgentCallbacks(
        usage_accumulator=UsageAccumulator(),
        generator_manager=generator,
        session_manager=None,
        trace_id="trace-1",
        run_input=_build_run_input(),
    )

    pending_call = LLMToolCall(
        id="call-1",
        type="function",
        function={"name": "ask_user_confirm", "arguments": "{\"question\":\"go?\"}"},
    )

    await callbacks.on_agent_interrupt(
        AgentResult(
            message=LLMMessage(role="assistant", tool_calls=[pending_call.model_dump(mode="json")]),
            steps=[],
            usage=LLMUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            client_tool_calls=[
                AgentClientToolCall(
                    tool_call_id="call-1",
                    name="ask_user_confirm",
                    arguments={"question": "go?"},
                )
            ],
            outcome="interrupted",
        )
    )

    run_finished = await generator.get()
    payload = run_finished.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert payload["type"] == "RUN_FINISHED"
    assert payload["outcome"] == "interrupt"
    assert payload["interrupt"]["payload"]["toolCalls"][0]["toolCallId"] == "call-1"
    assert payload["interrupt"]["payload"]["toolCalls"][0]["name"] == "ask_user_confirm"


@pytest.mark.asyncio
async def test_cancel_event_is_cancelled_outcome_not_interrupt():
    generator = AsyncGeneratorManager()
    callbacks = PersistingAgentCallbacks(
        usage_accumulator=UsageAccumulator(),
        generator_manager=generator,
        session_manager=None,
        trace_id="trace-1",
        run_input=_build_run_input(),
    )

    await callbacks.on_agent_cancel(
        AgentResult(
            message=LLMMessage(role="assistant", content="partial"),
            steps=[],
            usage=LLMUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            outcome="cancelled",
        )
    )

    run_finished = await generator.get()
    payload = run_finished.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert payload["type"] == "RUN_FINISHED"
    assert payload["outcome"] == "cancelled"
    assert "interrupt" not in payload


@pytest.mark.asyncio
async def test_step_thought_is_persisted_once_before_tool_result():
    session_manager = _FakeSessionManager()
    callbacks = PersistingAgentCallbacks(
        usage_accumulator=UsageAccumulator(),
        generator_manager=AsyncGeneratorManager(),
        session_manager=session_manager,
        trace_id="trace-1",
    )

    tool_call = LLMToolCall(
        id="call-1",
        type="function",
        function={"name": "fetch", "arguments": "{}"},
    )
    await callbacks.on_agent_step(
        AgentStep(
            thought="plan before tools",
            action=tool_call,
            observation={"ok": True},
        )
    )

    assert len(session_manager.buffered) == 2
    assert session_manager.buffered[0]["role"].value == "reasoning"
    assert session_manager.buffered[0]["text_content"] == "plan before tools"
    assert session_manager.buffered[1]["role"].value == "tool"
    assert session_manager.buffered[1]["tool_call_id"] == "call-1"


@pytest.mark.asyncio
async def test_finish_emits_reasoning_events_from_result_when_no_chunk_stream():
    generator = AsyncGeneratorManager()
    callbacks = PersistingAgentCallbacks(
        usage_accumulator=UsageAccumulator(),
        generator_manager=generator,
        session_manager=None,
        trace_id="trace-1",
        run_input=_build_run_input(),
    )

    await callbacks.on_agent_finish(
        AgentResult(
            message=LLMMessage(role="assistant", content="final"),
            steps=[],
            usage=LLMUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            reasoning_content="silent-thought",
            outcome="completed",
        )
    )

    events = [await generator.get() for _ in range(7)]
    payloads = [event.model_dump(mode="json", by_alias=True, exclude_none=True) for event in events]
    assert payloads[0]["type"] == "REASONING_START"
    assert payloads[1]["type"] == "REASONING_MESSAGE_START"
    assert payloads[2]["type"] == "REASONING_MESSAGE_CONTENT"
    assert payloads[2]["delta"] == "silent-thought"
    assert payloads[3]["type"] == "REASONING_MESSAGE_END"
    assert payloads[4]["type"] == "REASONING_END"
    assert payloads[5]["type"] == "RUN_FINISHED"


@pytest.mark.asyncio
async def test_tool_call_chunk_stream_emits_incremental_args_and_no_duplicate_full_args():
    generator = AsyncGeneratorManager()
    callbacks = PersistingAgentCallbacks(
        usage_accumulator=UsageAccumulator(),
        generator_manager=generator,
        session_manager=None,
        trace_id="trace-1",
        run_input=_build_run_input(),
    )

    await callbacks.on_tool_call_chunk_generated(
        LLMToolCallChunk(
            index=0,
            tool_call_id="call-1",
            tool_name="ask_user_confirm",
            arguments_delta='{"question":"go',
        )
    )
    await callbacks.on_tool_call_chunk_generated(
        LLMToolCallChunk(
            index=0,
            arguments_delta='?"}',
        )
    )
    await callbacks.on_tool_calls_generated(
        [
            LLMToolCall(
                id="call-1",
                type="function",
                function={"name": "ask_user_confirm", "arguments": "{\"question\":\"go?\"}"},
            )
        ]
    )

    payloads = [
        (await generator.get()).model_dump(mode="json", by_alias=True, exclude_none=True)
        for _ in range(4)
    ]
    assert [item["type"] for item in payloads] == [
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
    ]
    assert payloads[1]["delta"] == '{"question":"go'
    assert payloads[2]["delta"] == '?"}'
