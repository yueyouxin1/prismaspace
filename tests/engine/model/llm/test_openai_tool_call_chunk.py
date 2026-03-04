from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.engine.model.llm.clients.openai_client import OpenAIClient


def _tool_delta(index: int, tool_call_id=None, tool_name=None, arguments_delta=None):
    return SimpleNamespace(
        index=index,
        id=tool_call_id,
        function=SimpleNamespace(name=tool_name, arguments=arguments_delta),
    )


def _stream_chunk(tool_calls=None, finish_reason=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    tool_calls=tool_calls,
                    content=None,
                    reasoning_content=None,
                    reasoning=None,
                    thinking=None,
                ),
                finish_reason=finish_reason,
            )
        ],
        usage=None,
    )


async def _aiter(items):
    for item in items:
        yield item


pytestmark = pytest.mark.asyncio


async def test_openai_stream_tool_call_emits_tool_call_chunk_callbacks(
    monkeypatch,
    mock_openai_provider_config,
    mock_stream_run_config,
    mock_messages,
):
    mock_sdk = AsyncMock()
    monkeypatch.setattr("app.engine.model.llm.clients.openai_client.openai.AsyncOpenAI", lambda **kwargs: mock_sdk)
    mock_sdk.chat.completions.create.return_value = _aiter(
        [
            _stream_chunk(
                tool_calls=[_tool_delta(index=0, tool_call_id="call_1", tool_name="get_weather", arguments_delta='{"city":"bei')]
            ),
            _stream_chunk(tool_calls=[_tool_delta(index=0, arguments_delta='jing"}')]),
            _stream_chunk(finish_reason="tool_calls"),
        ]
    )

    callbacks = AsyncMock()
    client = OpenAIClient(mock_openai_provider_config)
    result = await client.generate(mock_stream_run_config, mock_messages, callbacks)

    assert callbacks.on_tool_call_chunk.await_count == 2
    first_chunk = callbacks.on_tool_call_chunk.await_args_list[0].args[0]
    second_chunk = callbacks.on_tool_call_chunk.await_args_list[1].args[0]
    assert first_chunk.tool_call_id == "call_1"
    assert first_chunk.tool_name == "get_weather"
    assert first_chunk.arguments_delta == '{"city":"bei'
    assert second_chunk.tool_call_id is None
    assert second_chunk.arguments_delta == 'jing"}'

    callbacks.on_tool_calls_generated.assert_called_once()
    final_calls = callbacks.on_tool_calls_generated.call_args.args[0]
    assert final_calls[0].id == "call_1"
    assert final_calls[0].function["name"] == "get_weather"
    assert final_calls[0].function["arguments"] == '{"city":"beijing"}'
    assert result.message.tool_calls[0]["function"]["arguments"] == '{"city":"beijing"}'
