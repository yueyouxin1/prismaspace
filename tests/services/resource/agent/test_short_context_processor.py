from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.interaction.chat import MessageRole
from app.services.resource.agent.agent_session_manager import AgentSessionManager
from app.services.resource.agent.processors import (
    AgentPipelineContext,
    CustomHistoryMergeProcessor,
    ShortContextProcessor,
    ToolChainAlignmentProcessor,
)
from app.engine.model.llm import LLMMessage


@pytest.mark.asyncio
async def test_short_context_processor_filters_activity_messages_from_llm_history():
    activity_message = SimpleNamespace(
        role=MessageRole.ACTIVITY,
        turn_id="turn-1",
        text_content="searching",
        content=None,
        content_parts=None,
        tool_calls=None,
        tool_call_id=None,
    )
    assistant_message = SimpleNamespace(
        role=MessageRole.ASSISTANT,
        turn_id="turn-1",
        text_content="answer",
        content=None,
        content_parts=None,
        tool_calls=None,
        tool_call_id=None,
    )

    session_manager = SimpleNamespace(
        session=SimpleNamespace(id=1, turn_count=1, message_count=2),
        get_recent_messages=AsyncMock(return_value=[activity_message, assistant_message]),
    )

    processor = ShortContextProcessor(
        context=SimpleNamespace(),
        session_manager=session_manager,
        max_turns=2,
    )
    ctx = AgentPipelineContext()

    await processor.process(ctx)

    assert len(ctx.history) == 1
    assert ctx.history[0].role == "assistant"
    assert ctx.history[0].content == "answer"


@pytest.mark.asyncio
async def test_short_context_processor_respects_single_turn_history_setting():
    latest_turn_messages = [
        SimpleNamespace(
            role=MessageRole.USER,
            turn_id="turn-3",
            text_content="latest question",
            content=None,
            content_parts=None,
            tool_calls=None,
            tool_call_id=None,
        ),
        SimpleNamespace(
            role=MessageRole.ASSISTANT,
            turn_id="turn-3",
            text_content="latest answer",
            content=None,
            content_parts=None,
            tool_calls=None,
            tool_call_id=None,
        ),
    ]
    session_manager = SimpleNamespace(
        session=SimpleNamespace(id=1, turn_count=3, message_count=12),
        get_recent_messages=AsyncMock(return_value=latest_turn_messages),
    )

    processor = ShortContextProcessor(
        context=SimpleNamespace(),
        session_manager=session_manager,
        max_turns=1,
    )
    ctx = AgentPipelineContext()

    await processor.process(ctx)

    session_manager.get_recent_messages.assert_awaited_once_with(1)
    assert [message.turn_id for message in latest_turn_messages] == ["turn-3", "turn-3"]
    assert [message.role for message in ctx.history] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_agent_session_manager_recent_turn_cache_reuses_prefetched_snapshot():
    session_manager = object.__new__(AgentSessionManager)
    session_manager.session = SimpleNamespace(id=1)
    session_manager.session_service = SimpleNamespace(
        get_recent_messages=AsyncMock(
            return_value=[
                SimpleNamespace(turn_id="turn-1"),
                SimpleNamespace(turn_id="turn-1"),
                SimpleNamespace(turn_id="turn-2"),
                SimpleNamespace(turn_id="turn-3"),
                SimpleNamespace(turn_id="turn-3"),
            ]
        )
    )
    session_manager._recent_turn_messages_cache = []
    session_manager._recent_turn_messages_cache_turns = 0

    await session_manager.preload_recent_messages(3)
    recent_messages = await session_manager.get_recent_messages(2)

    assert [message.turn_id for message in recent_messages] == ["turn-2", "turn-3", "turn-3"]
    session_manager.session_service.get_recent_messages.assert_awaited_once_with(1, limit=3)


@pytest.mark.asyncio
async def test_short_context_processor_appends_custom_history_after_session_history():
    processor = CustomHistoryMergeProcessor(
        custom_history=[LLMMessage(role="tool", content='{"approved":true}', tool_call_id="call-1")]
    )
    ctx = AgentPipelineContext(
        history=[LLMMessage(role="assistant", content="session answer")]
    )

    await processor.process(ctx)

    assert [m.role for m in ctx.history] == ["assistant", "tool"]
    assert ctx.history[1].tool_call_id == "call-1"


@pytest.mark.asyncio
async def test_tool_chain_alignment_reorders_tool_results_by_declared_call_order():
    processor = ToolChainAlignmentProcessor()
    ctx = AgentPipelineContext(
        history=[
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[
                    {"id": "call-1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
                    {"id": "call-2", "type": "function", "function": {"name": "b", "arguments": "{}"}},
                ],
            ),
            LLMMessage(role="tool", content='{"ok":2}', tool_call_id="call-2"),
            LLMMessage(role="tool", content='{"ok":1}', tool_call_id="call-1"),
            LLMMessage(role="assistant", content="done"),
        ]
    )

    await processor.process(ctx)

    assert [msg.role for msg in ctx.history] == ["assistant", "tool", "tool", "assistant"]
    assert [call["id"] for call in (ctx.history[0].tool_calls or [])] == ["call-1", "call-2"]
    assert [ctx.history[1].tool_call_id, ctx.history[2].tool_call_id] == ["call-1", "call-2"]


@pytest.mark.asyncio
async def test_tool_chain_alignment_keeps_partial_valid_pairs_and_drops_unresolved_calls():
    processor = ToolChainAlignmentProcessor()
    ctx = AgentPipelineContext(
        history=[
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[
                    {"id": "call-1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
                    {"id": "call-2", "type": "function", "function": {"name": "b", "arguments": "{}"}},
                ],
            ),
            LLMMessage(role="tool", content='{"ok":1}', tool_call_id="call-1"),
            LLMMessage(role="user", content="next question"),
        ]
    )

    await processor.process(ctx)

    assert [msg.role for msg in ctx.history] == ["assistant", "tool", "user"]
    assert [call["id"] for call in (ctx.history[0].tool_calls or [])] == ["call-1"]
    assert ctx.history[1].tool_call_id == "call-1"


@pytest.mark.asyncio
async def test_tool_chain_alignment_drops_dangling_tool_messages_without_active_tool_call_block():
    processor = ToolChainAlignmentProcessor()
    ctx = AgentPipelineContext(
        history=[
            LLMMessage(role="tool", content='{"orphan":true}', tool_call_id="call-x"),
            LLMMessage(role="user", content="hello"),
            LLMMessage(role="assistant", content="hi"),
            LLMMessage(role="tool", content='{"orphan":true}', tool_call_id="call-y"),
        ]
    )

    await processor.process(ctx)

    assert [msg.role for msg in ctx.history] == ["user", "assistant"]
