from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.engine.model.llm import LLMMessage
from app.models.interaction.chat import MessageRole
from app.schemas.resource.agent.agent_schemas import DeepMemoryConfig
from app.services.resource.agent.processors import AgentPipelineContext, DeepMemoryProcessor


@pytest.mark.asyncio
async def test_deep_memory_processor_injects_recalled_turns_into_dynamic_context():
    processor = object.__new__(DeepMemoryProcessor)
    processor.context = SimpleNamespace(actor=SimpleNamespace(id=42))
    processor.session_manager = SimpleNamespace(
        session=SimpleNamespace(uuid="session-1"),
        agent_instance=SimpleNamespace(id=7),
        runtime_workspace=SimpleNamespace(id=9),
    )
    processor.config = DeepMemoryConfig(
        enabled=True,
        enable_vector_recall=True,
        enable_summarization=False,
    )

    recalled_turn = [
        SimpleNamespace(
            role=MessageRole.USER,
            trace_id="trace-1",
            text_content="hello",
            content=None,
            content_parts=None,
        ),
        SimpleNamespace(
            role=MessageRole.ASSISTANT,
            trace_id="trace-1",
            text_content="world",
            content=None,
            content_parts=None,
        ),
    ]
    processor.long_term_service = SimpleNamespace(
        retrieve=AsyncMock(return_value=[recalled_turn])
    )
    processor.summary_service = SimpleNamespace(
        list_summaries=AsyncMock(return_value=[])
    )

    ctx = AgentPipelineContext(
        user_message=LLMMessage(role="user", content="what happened"),
        exclude_trace_ids={"trace-old"},
    )

    await processor.process(ctx)

    processor.long_term_service.retrieve.assert_awaited_once()
    assert ctx.dynamic_contexts
    assert "### Recalled Conversation:" in ctx.dynamic_contexts[0]
    assert "user: hello" in ctx.dynamic_contexts[0]
    assert "assistant: world" in ctx.dynamic_contexts[0]
