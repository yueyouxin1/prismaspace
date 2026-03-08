# tests/engine/model/llm/test_llm_engine_service.py
import pytest
from unittest.mock import AsyncMock, Mock

from app.engine.model.llm import LLMEngineService, LLMProviderConfig
from app.engine.model.llm.base import BaseLLMClient, LLMEngineError, LLMProviderNotFoundError 
from app.engine.model.llm.main import _llm_clients_registry

# 标记所有测试为异步
pytestmark = pytest.mark.asyncio


class MockLLMClient(BaseLLMClient):
    """一个用于测试的、可控制行为的模拟客户端。"""
    def __init__(self, config):
        self.config = config
        self.generate_mock = AsyncMock()
        self.close_mock = AsyncMock()

    async def generate(self, run_config, messages, callbacks):
        await self.generate_mock(run_config, messages, callbacks)

    async def aclose(self):
        await self.close_mock()


@pytest.fixture(autouse=True)
async def clear_cached_clients():
    await LLMEngineService.close_cached_clients()
    yield
    await LLMEngineService.close_cached_clients()


async def test_engine_selects_correct_client(monkeypatch, mock_openai_provider_config, mock_stream_run_config, mock_messages):
    """测试引擎是否能根据 client_name 正确选择和实例化客户端。"""
    # 1. 设置
    engine = LLMEngineService()
    # 使用 mocker 替换掉 _get_client 内部的客户端实例化逻辑，返回我们的 MockLLMClient
    mock_client_instance = MockLLMClient(mock_openai_provider_config)
    get_client_mock = Mock(return_value=mock_client_instance)
    monkeypatch.setattr(engine, "_get_client", get_client_mock)
    
    # 2. 执行
    callbacks = AsyncMock()
    await engine.run(
        provider_config=mock_openai_provider_config,
        run_config=mock_stream_run_config,
        messages=mock_messages,
        callbacks=callbacks
    )

    # 3. 断言
    # 验证 _get_client 被正确调用
    get_client_mock.assert_called_once_with(mock_openai_provider_config)
    # 验证模拟客户端的 generate 方法被调用，证明调度成功
    mock_client_instance.generate_mock.assert_called_once()


# [*** 开始修复 ***]

async def test_engine_handles_unknown_provider(mock_stream_run_config, mock_messages): # <-- 注入 fixtures
    """测试当请求一个未注册的 provider 时，引擎是否会抛出 ValueError。"""
    # 1. 设置
    engine = LLMEngineService()
    # 使用 Pydantic 模型，而不是字典
    unknown_provider_config = LLMProviderConfig(client_name="unknown_client", api_key="fake")
    
    # 2. 执行 & 断言
    with pytest.raises(LLMProviderNotFoundError, match="No LLM client registered for provider 'unknown_client'"):
        await engine.run(
            provider_config=unknown_provider_config,
            run_config=mock_stream_run_config, # <-- 使用 fixture
            messages=mock_messages,           # <-- 使用 fixture
            callbacks=AsyncMock()
        )


async def test_engine_propagates_client_errors(monkeypatch, mock_openai_provider_config, mock_stream_run_config, mock_messages): # <-- 注入 fixtures
    """测试如果客户端在执行中抛出异常，引擎是否会捕获并报告它。"""
    # 1. 设置
    engine = LLMEngineService()
    mock_client_instance = MockLLMClient(mock_openai_provider_config)
    # 让模拟的 generate 方法抛出一个预期的异常
    test_exception = LLMEngineError("Client failed")
    mock_client_instance.generate_mock.side_effect = test_exception
    get_client_mock = Mock(return_value=mock_client_instance)
    monkeypatch.setattr(engine, "_get_client", get_client_mock)
    callbacks = AsyncMock()
    
    # 2. 执行 & 断言
    with pytest.raises(LLMEngineError, match="Client failed"):
        await engine.run(
            provider_config=mock_openai_provider_config,
            run_config=mock_stream_run_config, # <-- 使用 fixture
            messages=mock_messages,           # <-- 使用 fixture
            callbacks=callbacks
        )
    
    # 验证 on_error 回调被调用
    callbacks.on_error.assert_called_once()
    # 改进断言，确保回调接收到的正是我们抛出的那个异常实例
    assert isinstance(callbacks.on_error.call_args[0][0], LLMEngineError)
    assert "Client failed" in str(callbacks.on_error.call_args[0][0])
    
# [*** 结束修复 ***]


async def test_engine_uses_context_manager_with_reserved_budget(
    monkeypatch,
    mock_openai_provider_config,
    mock_stream_run_config,
    mock_messages,
):
    engine = LLMEngineService()
    mock_client_instance = MockLLMClient(mock_openai_provider_config)
    get_client_mock = Mock(return_value=mock_client_instance)
    monkeypatch.setattr(engine, "_get_client", get_client_mock)
    manage_mock = Mock(return_value=mock_messages)
    monkeypatch.setattr(engine.context_manager, "manage", manage_mock)

    run_config = mock_stream_run_config.model_copy(
        update={"max_context_window": 4096, "max_tokens": 1500}
    )
    await engine.run(
        provider_config=mock_openai_provider_config,
        run_config=run_config,
        messages=mock_messages,
        callbacks=AsyncMock(),
    )

    manage_mock.assert_called_once_with(
        messages=mock_messages,
        provider=mock_openai_provider_config.client_name,
        model=run_config.model,
        max_context_tokens=4096,
        reserve_tokens=1500,
    )


async def test_engine_reuses_client_for_same_provider_config(monkeypatch, mock_openai_provider_config):
    engine = LLMEngineService()

    class CachedClient(MockLLMClient):
        instances = []

        def __init__(self, config):
            super().__init__(config)
            self.__class__.instances.append(self)

    monkeypatch.setitem(_llm_clients_registry, "openai", CachedClient)

    client1 = engine._get_client(mock_openai_provider_config)
    client2 = engine._get_client(mock_openai_provider_config.model_copy())

    assert client1 is client2
    assert len(CachedClient.instances) == 1


async def test_engine_keeps_clients_isolated_for_different_provider_configs(monkeypatch, mock_openai_provider_config):
    engine = LLMEngineService()

    class CachedClient(MockLLMClient):
        instances = []

        def __init__(self, config):
            super().__init__(config)
            self.__class__.instances.append(self)

    monkeypatch.setitem(_llm_clients_registry, "openai", CachedClient)

    other_config = mock_openai_provider_config.model_copy(update={"api_key": "another-key"})

    client1 = engine._get_client(mock_openai_provider_config)
    client2 = engine._get_client(other_config)

    assert client1 is not client2
    assert len(CachedClient.instances) == 2


async def test_engine_close_cached_clients_closes_all_reused_clients(monkeypatch, mock_openai_provider_config):
    engine = LLMEngineService()

    class CachedClient(MockLLMClient):
        instances = []

        def __init__(self, config):
            super().__init__(config)
            self.__class__.instances.append(self)

    monkeypatch.setitem(_llm_clients_registry, "openai", CachedClient)

    engine._get_client(mock_openai_provider_config)
    engine._get_client(mock_openai_provider_config.model_copy())

    assert len(CachedClient.instances) == 1

    await LLMEngineService.close_cached_clients()

    CachedClient.instances[0].close_mock.assert_awaited_once()
