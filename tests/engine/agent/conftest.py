# tests/engine/agent/conftest.py
import pytest
import json
from unittest.mock import AsyncMock

from app.engine.agent.base import BaseToolExecutor, AgentInput
from app.engine.model.llm import (
    LLMEngineService, LLMMessage, LLMToolCall, LLMProviderConfig, LLMRunConfig
)

# --- Mock Implementations ---

class MockLLMEngine(LLMEngineService):
    """A mock LLM Engine that can be programmed with a sequence of responses."""
    def __init__(self):
        self.responses = []
        self.call_history = []

    def set_responses(self, responses: list):
        """Pre-program the responses. Can be tool calls or final answers."""
        self.responses = responses

    async def run(self, provider_config, run_config, messages, callbacks):
        self.call_history.append(messages.copy())
        if not self.responses:
            raise Exception("MockLLMEngine has no more responses to give.")
        
        response_type, response_data = self.responses.pop(0)
        
        # Simple, predictable behavior for tests.
        if response_type == "tool_calls":
            await callbacks.on_tool_calls_generated(response_data)
        elif response_type == "final_answer":
            await callbacks.on_chunk_generated(response_data.content)
            await callbacks.on_success(response_data)
        else:
            await callbacks.on_error(Exception("Unknown mock response type"))

class MockToolExecutor(BaseToolExecutor):
    def __init__(self):
        self.execute_mock = AsyncMock(return_value={"status": "success"})

    async def execute(self, tool_name: str, tool_args: dict) -> dict:
        return await self.execute_mock(tool_name, tool_args)

class MockContextProcessor():
    def __init__(self):
        self.process_mock = AsyncMock()
        # This default is fine, as long as the test can override it.
        self.process_mock.side_effect = lambda messages: messages

    async def process(self, messages: list) -> list:
        # We allow overriding the side_effect in tests that need it.
        return await self.process_mock(messages)

# --- Pytest Fixtures ---

@pytest.fixture
def mock_llm_engine():
    return MockLLMEngine()

@pytest.fixture
def mock_tool_executor():
    return MockToolExecutor()

@pytest.fixture
def mock_context_processor():
    return MockContextProcessor()

@pytest.fixture
def mock_agent_callbacks():
    # Using AsyncMock allows us to inspect calls to the callback methods
    return AsyncMock()

@pytest.fixture
def mock_provider_config():
    return LLMProviderConfig(client_name="mock", api_key="mock")

@pytest.fixture
def mock_run_config():
    return LLMRunConfig(model="mock")

@pytest.fixture
def mock_initial_agent_input():
    return AgentInput(messages=[LLMMessage(role="user", content="What's the weather in Beijing?")])