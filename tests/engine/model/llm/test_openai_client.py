# tests/engine/model/llm/test_openai_client.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from openai import RateLimitError, BadRequestError
from openai.types.chat import ChatCompletion, ChatCompletionChunk
from openai.types.chat.chat_completion import Choice
# [*** 1. 导入需要的 Pydantic 模型 ***]
from openai.types.chat.chat_completion_message import ChatCompletionMessage
from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall, Function
from openai.types.chat.chat_completion_chunk import ChoiceDelta, Choice as ChunkChoice
from openai.types.completion_usage import CompletionUsage

from app.engine.model.llm.clients.openai_client import OpenAIClient
from app.engine.model.llm.base import (
    LLMRateLimitError, LLMBadRequestError, LLMContextLengthExceededError,
    LLMToolCall
)

pytestmark = pytest.mark.asyncio

# --- 辅助函数 ---

def create_mock_stream_chunk(content: str = None, tool_calls=None, finish_reason=None):
    """创建一个模拟的流式响应块。"""
    delta = ChoiceDelta(content=content, tool_calls=tool_calls)
    return ChatCompletionChunk(
        id="fake-id", choices=[ChunkChoice(delta=delta, index=0, finish_reason=finish_reason)],
        created=123, model="gpt-4o", object="chat.completion.chunk"
    )

# [*** 2. 修改 create_mock_completion 函数 ***]
def create_mock_completion(content: str = None, tool_calls=None):
    """创建一个模拟的、符合 Pydantic 模型的非流式完整响应。"""
    
    # 构建 ChatCompletionMessage 的参数
    message_args = {"role": "assistant"} # role 是必需的
    if content:
        message_args["content"] = content
    
    if tool_calls:
        # 将模拟的 tool_calls 数据转换为真实的 Pydantic 对象
        actual_tool_calls = []
        for tc_mock in tool_calls:
            actual_tool_calls.append(
                ChatCompletionMessageToolCall(
                    id=tc_mock.id,
                    type=tc_mock.type,
                    function=Function(
                        name=tc_mock.function.name,
                        arguments=tc_mock.function.arguments
                    )
                )
            )
        message_args["tool_calls"] = actual_tool_calls

    # 创建一个真实的 ChatCompletionMessage 实例
    message_instance = ChatCompletionMessage(**message_args)

    return ChatCompletion(
        id="fake-id",
        choices=[Choice(
            finish_reason="stop", index=0,
            message=message_instance # 传入真实的 Pydantic 实例
        )],
        created=123, model="gpt-4o", object="chat.completion",
        usage=CompletionUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
    )

async def mock_async_iterator(items):
    """将一个列表转换为异步迭代器。"""
    for item in items:
        yield item

# --- Fixture for mocked client ---
@pytest.fixture
def mock_openai_sdk_client(mocker):
    """一个模拟的 openai.AsyncOpenAI 实例的 fixture。"""
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock()
    mocker.patch("app.engine.model.llm.clients.openai_client.openai.AsyncOpenAI", return_value=mock_client)
    return mock_client

# --- 测试用例 (保持不变) ---
async def test_generate_stream_text(mock_openai_sdk_client, mock_openai_provider_config, mock_stream_run_config, mock_messages):
    """测试流式生成文本的成功路径。"""
    mock_stream_response = mock_async_iterator([
        create_mock_stream_chunk(content="Hello"),
        create_mock_stream_chunk(content=" world"),
        create_mock_stream_chunk(finish_reason="stop")
    ])
    mock_openai_sdk_client.chat.completions.create.return_value = mock_stream_response

    client = OpenAIClient(mock_openai_provider_config)
    callbacks = AsyncMock()
    await client.generate(mock_stream_run_config, mock_messages, callbacks)

    mock_openai_sdk_client.chat.completions.create.assert_called_once()
    assert callbacks.on_chunk_generated.call_count == 2
    callbacks.on_chunk_generated.assert_any_call("Hello")
    callbacks.on_chunk_generated.assert_any_call(" world")
    callbacks.on_success.assert_called_once()
    final_message = callbacks.on_success.call_args[0][0]
    assert final_message.role == "assistant"
    assert final_message.content == "Hello world"


async def test_generate_non_stream_tool_call(mock_openai_sdk_client, mock_openai_provider_config, mock_tool_run_config, mock_messages):
    """测试非流式生成工具调用的成功路径。"""
    mock_tool_call_obj = MagicMock()
    mock_tool_call_obj.id = "call_123"
    mock_tool_call_obj.type = "function"
    mock_tool_call_obj.function.name = "get_weather"
    mock_tool_call_obj.function.arguments = '{"location": "beijing"}'
    
    mock_response = create_mock_completion(tool_calls=[mock_tool_call_obj])
    mock_openai_sdk_client.chat.completions.create.return_value = mock_response
    
    client = OpenAIClient(mock_openai_provider_config)
    callbacks = AsyncMock()
    
    await client.generate(mock_tool_run_config, mock_messages, callbacks)
    
    callbacks.on_tool_calls_generated.assert_called_once()
    tool_calls_arg = callbacks.on_tool_calls_generated.call_args[0][0]
    assert len(tool_calls_arg) == 1
    assert tool_calls_arg[0].id == "call_123"
    callbacks.on_usage.assert_called_once()


async def test_generate_handles_rate_limit_error(mock_openai_sdk_client, mock_openai_provider_config, mock_stream_run_config, mock_messages):
    """测试当 API 返回 RateLimitError 时，客户端是否能正确转换为 LLMRateLimitError。"""
    mock_openai_sdk_client.chat.completions.create.side_effect = RateLimitError("Rate limit exceeded", response=MagicMock(), body=None)
    
    client = OpenAIClient(mock_openai_provider_config)
    callbacks = AsyncMock()
    
    with pytest.raises(LLMRateLimitError, match="OpenAI rate limit exceeded"):
        await client.generate(mock_stream_run_config, mock_messages, callbacks)


async def test_generate_handles_context_length_error(mock_openai_sdk_client, mock_openai_provider_config, mock_stream_run_config, mock_messages):
    """测试当 API 返回特定 code 的 BadRequestError 时，是否能正确转换为 LLMContextLengthExceededError。"""
    mock_error = BadRequestError("Context length exceeded", response=MagicMock(), body={"code": "context_length_exceeded"})
    mock_openai_sdk_client.chat.completions.create.side_effect = mock_error
    
    client = OpenAIClient(mock_openai_provider_config)
    callbacks = AsyncMock()
    
    with pytest.raises(LLMContextLengthExceededError, match="Context length exceeded"):
        await client.generate(mock_stream_run_config, mock_messages, callbacks)