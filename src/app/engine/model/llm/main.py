# src/app/engine/model/llm/main.py

import asyncio
import hashlib
import logging
import threading
from typing import Dict, Type, List, Optional, Tuple
from .base import (
    BaseLLMClient, LLMProviderConfig, LLMRunConfig, LLMMessage, LLMResult, 
    LLMEngineCallbacks, LLMEngineError, LLMProviderNotFoundError 
)
from .context import LLMContextManager

# --- 客户端注册表 ---
_llm_clients_registry: Dict[str, Type[BaseLLMClient]] = {}

def register_llm_client(client_name: str):
    """一个装饰器，用于将具体的客户端实现注册到工厂中。"""
    def decorator(cls: Type[BaseLLMClient]):
        _llm_clients_registry[client_name] = cls
        return cls
    return decorator


class LLMEngineService:
    """
    纯粹的、无状态的LLM执行引擎。
    它现在集成了上下文管理器，以确保请求在发送前是合规的。
    """
    _client_cache: Dict[Tuple[str, str, str, int, int], BaseLLMClient] = {}
    _client_cache_lock = threading.Lock()

    def __init__(self):
        self.context_manager = LLMContextManager() # 实例化上下文管理器

    @staticmethod
    def _build_client_cache_key(config: LLMProviderConfig) -> Tuple[str, str, str, int, int]:
        api_key_hash = hashlib.sha256(config.api_key.encode("utf-8")).hexdigest()
        base_url = str(config.base_url) if config.base_url else ""
        return (
            config.client_name,
            api_key_hash,
            base_url,
            config.timeout,
            config.max_retries,
        )

    def _get_client(self, config: LLMProviderConfig) -> BaseLLMClient:
        """
        工厂方法：根据提供商名称查找并实例化客户端。
        """
        client_name = config.client_name
        client_class = _llm_clients_registry.get(client_name)
        
        if not client_class:
            raise LLMProviderNotFoundError(
                f"No LLM client registered for provider '{client_name}'. "
                f"Available providers: {list(_llm_clients_registry.keys())}"
            )

        cache_key = self._build_client_cache_key(config)
        with self._client_cache_lock:
            client = self._client_cache.get(cache_key)
            if client is not None:
                return client

            client = client_class(config)
            self._client_cache[cache_key] = client
            return client

    @classmethod
    async def close_cached_clients(cls) -> None:
        with cls._client_cache_lock:
            clients = list(cls._client_cache.values())
            cls._client_cache.clear()

        for client in clients:
            try:
                await client.aclose()
            except Exception as exc:
                logging.getLogger(__name__).warning("Failed to close cached LLM client: %s", exc)

    async def run(
        self,
        provider_config: LLMProviderConfig,
        run_config: LLMRunConfig,
        messages: List[LLMMessage],
        callbacks: Optional[LLMEngineCallbacks] = None,
    ) -> LLMResult:
        try:
            # 步骤 1: 使用上下文管理器处理消息 (如果设置了 max_context_window)
            managed_messages = messages
            if run_config.max_context_window:
                reserve_tokens = max(500, run_config.max_tokens)
                reserve_tokens = min(reserve_tokens, max(run_config.max_context_window // 2, 1))
                managed_messages = self.context_manager.manage(
                    messages=messages,
                    provider=provider_config.client_name,
                    model=run_config.model,
                    max_context_tokens=run_config.max_context_window,
                    reserve_tokens=reserve_tokens,
                )

            # 步骤 2: 获取客户端实例
            client = self._get_client(provider_config)

            # 步骤 3: 调用客户端的 generate 方法
            if callbacks: await callbacks.on_start()
            result: LLMResult = await client.generate(
                run_config=run_config,
                messages=managed_messages, # 使用处理过的消息
                callbacks=callbacks
            )
            if callbacks: await callbacks.on_success(result)
            return result
        except asyncio.CancelledError:
            # [关键] 捕获取消信号
            # 1. 记录日志
            logging.info(f"LLM generation cancelled for model {run_config.model}")
            # 2. 我们不调用 callbacks.on_error，因为这不是一个错误
            # 3. 重新抛出异常，以便上层（AgentEngine）也能感知并停止循环
            raise 
        except LLMEngineError as e:
            # 捕获我们自己定义的、可预期的引擎错误
            if callbacks: await callbacks.on_error(e)
            raise
        except Exception as e:
            # 捕获在客户端选择或上下文管理期间的任何意外错误
            err = LLMEngineError(f"An unexpected error occurred in LLMEngineService: {str(e)}")
            if callbacks: await callbacks.on_error(err)
            raise err
