# src/app/engine/model/llm/main.py

import asyncio
import logging
from typing import Dict, Type, List, Optional
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
    def __init__(self):
        self.context_manager = LLMContextManager() # 实例化上下文管理器
    
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
        
        # 每次调用都创建一个新的客户端实例，以确保配置隔离
        return client_class(config)

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
                managed_messages = self.context_manager.manage(
                    messages=messages,
                    provider=config.client_name,
                    model=run_config.model,
                    max_context_tokens=run_config.max_context_window,
                    max_tokens=run_config.max_tokens
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