import pytest

from app.engine.agent import AgentResult
from app.engine.model.llm import LLMMessage, LLMUsage
from app.services.common.llm_capability_provider import UsageAccumulator
from app.services.resource.agent.agent_service import PersistingAgentCallbacks
from app.utils.async_generator import AsyncGeneratorManager


class _FakeSession:
    uuid = "session-1"


class _FakeSessionManager:
    def __init__(self):
        self.session = _FakeSession()
        self.buffered = []

    def buffer_message(self, role, content=None, tool_calls=None, tool_call_id=None, token_count=0, meta=None):
        self.buffered.append(
            {
                "role": role,
                "content": content,
                "tool_calls": tool_calls,
                "tool_call_id": tool_call_id,
                "token_count": token_count,
                "meta": meta,
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

    await callbacks.on_reasoning_chunk_generated("first ")
    await callbacks.on_reasoning_chunk_generated("second")
    await callbacks.on_agent_finish(
        AgentResult(
            message=LLMMessage(role="assistant", content="final answer"),
            steps=[],
            usage=LLMUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            outcome="completed",
        )
    )

    assert len(session_manager.buffered) == 1
    persisted = session_manager.buffered[0]
    assert persisted["content"] == "final answer"
    assert persisted["meta"]["reasoning"]["plaintext"] == "first second"
