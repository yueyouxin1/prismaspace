from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.engine.model.llm import LLMResult, LLMUsage
from app.engine.vector.base import VectorChunk
from app.engine.model.llm import LLMMessage
from app.models.interaction.chat import MessageRole
from app.schemas.resource.agent.agent_schemas import DeepMemoryConfig
from app.services.resource.agent.memory.deep.context_summary_service import ContextSummaryService
from app.services.resource.agent.memory.deep.long_term_context_service import (
    COLLECTION_NAME,
    LongTermContextService,
)
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
            turn_id="turn-1",
            text_content="hello",
            content=None,
            content_parts=None,
        ),
        SimpleNamespace(
            role=MessageRole.ASSISTANT,
            turn_id="turn-1",
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
        exclude_turn_ids={"turn-old"},
    )

    await processor.process(ctx)

    processor.long_term_service.retrieve.assert_awaited_once()
    assert ctx.dynamic_contexts
    assert "### Recalled Conversation:" in ctx.dynamic_contexts[0]
    assert "user: hello" in ctx.dynamic_contexts[0]
    assert "assistant: world" in ctx.dynamic_contexts[0]


@pytest.mark.asyncio
async def test_context_summary_background_invalidates_previous_turn_summary_before_create():
    service = object.__new__(ContextSummaryService)
    service.context = SimpleNamespace(actor=SimpleNamespace(id=42))
    service.instance_dao = SimpleNamespace(get_by_pk=AsyncMock(return_value=SimpleNamespace(id=7)))
    service.workspace_dao = SimpleNamespace(get_by_pk=AsyncMock(return_value=SimpleNamespace(id=9)))
    service.ai_provider = SimpleNamespace(
        resolve_model_version=AsyncMock(return_value=SimpleNamespace(id=11)),
        execute_llm_with_billing=AsyncMock(
            return_value=LLMResult(
                message=LLMMessage(role="assistant", content="summary"),
                usage=LLMUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )
        ),
    )
    service.module_service = SimpleNamespace(
        get_runtime_context=AsyncMock(return_value=SimpleNamespace(version=SimpleNamespace(name="gpt-summary")))
    )
    service.invalid_summary_for_turn = AsyncMock()
    service.create_summary_internal = AsyncMock()

    messages = [
        SimpleNamespace(role=MessageRole.USER, created_at="2026-03-07T00:00:00Z", text_content="hello", content=None, content_parts=None),
        SimpleNamespace(role=MessageRole.ASSISTANT, created_at="2026-03-07T00:00:01Z", text_content="world", content=None, content_parts=None),
    ]

    await service.summarize_turn_background(
        agent_instance_id=7,
        session_uuid="session-1",
        run_id="run-1",
        turn_id="turn-1",
        messages=messages,
        deep_memory_config=DeepMemoryConfig(enabled=True, enable_summarization=True),
        runtime_workspace_id=9,
        trace_id="trace-1",
    )

    service.invalid_summary_for_turn.assert_awaited_once_with(
        turn_id="turn-1",
        session_uuid="session-1",
        agent_instance_id=7,
        user_id=42,
        mode="production",
    )
    service.create_summary_internal.assert_awaited_once()


@pytest.mark.asyncio
async def test_long_term_context_background_deletes_stale_chunks_before_upsert():
    service = object.__new__(LongTermContextService)
    service.smv_dao = SimpleNamespace(
        get_default_version_by_type=AsyncMock(
            return_value=SimpleNamespace(id=3, attributes={"max_batch_tokens": 256, "dimensions": 2})
        )
    )
    service.instance_dao = SimpleNamespace(get_by_pk=AsyncMock(return_value=SimpleNamespace(id=7)))
    service.workspace_dao = SimpleNamespace(get_by_pk=AsyncMock(return_value=SimpleNamespace(id=9)))
    service.embedding_service = SimpleNamespace(
        generate_embedding=AsyncMock(
            return_value=SimpleNamespace(
                results=[
                    SimpleNamespace(vector=[0.1, 0.2]),
                    SimpleNamespace(vector=[0.3, 0.4]),
                ]
            )
        )
    )
    service.chunker = SimpleNamespace(
        run=AsyncMock(
            return_value=[
                SimpleNamespace(content="chunk-a"),
                SimpleNamespace(content="chunk-b"),
            ]
        )
    )
    engine = SimpleNamespace(
        query=AsyncMock(
            return_value=[
                VectorChunk(id="turn-1_chunk_0", vector=[], payload={}),
                VectorChunk(id="turn-1_chunk_3", vector=[], payload={}),
            ]
        ),
        delete=AsyncMock(),
        upsert=AsyncMock(),
    )
    service.vector_manager = SimpleNamespace(get_engine=AsyncMock(return_value=engine))

    messages = [
        SimpleNamespace(role=MessageRole.USER, text_content="hello", content=None, content_parts=None, tool_calls=None),
        SimpleNamespace(role=MessageRole.ASSISTANT, text_content="world", content=None, content_parts=None, tool_calls=None),
    ]

    await service.index_turn_background(
        agent_instance_id=7,
        session_uuid="session-1",
        run_id="run-1",
        turn_id="turn-1",
        messages=messages,
        runtime_workspace_id=9,
        trace_id="trace-1",
    )

    engine.query.assert_awaited_once()
    engine.delete.assert_awaited_once_with(COLLECTION_NAME, pks=["turn-1_chunk_3"])
    engine.upsert.assert_awaited_once()
