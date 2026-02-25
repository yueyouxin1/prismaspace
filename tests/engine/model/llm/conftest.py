# tests/engine/model/llm/conftest.py
import pytest
from app.engine.model.llm import (
    LLMProviderConfig,
    LLMRunConfig,
    LLMMessage,
    LLMTool,
)

@pytest.fixture
def mock_openai_provider_config():
    """一个标准的 OpenAI 提供商配置 fixture。"""
    return LLMProviderConfig(
        client_name="openai",
        api_key="fake-api-key",
        base_url="http://localhost:8080/v1"
    )

@pytest.fixture
def mock_stream_run_config():
    """一个流式运行配置 fixture。"""
    return LLMRunConfig(model="gpt-4o", stream=True)

@pytest.fixture
def mock_non_stream_run_config():
    """一个非流式运行配置 fixture。"""
    return LLMRunConfig(model="gpt-4o", stream=False)

@pytest.fixture
def mock_messages():
    """一个简单的消息列表 fixture。"""
    return [LLMMessage(role="user", content="Hello")]

@pytest.fixture
def mock_tool_run_config():
    """一个带工具的运行配置 fixture。"""
    return LLMRunConfig(
        model="gpt-4o",
        stream=False,
        tools=[
            LLMTool(function={
                "name": "get_weather",
                "description": "Get the current weather",
                "parameters": {"type": "object", "properties": {}}
            })
        ]
    )