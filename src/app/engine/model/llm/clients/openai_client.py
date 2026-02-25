# src/app/engine/model/llm/clients/openai_client.py

import openai
import asyncio
import json
from typing import List, AsyncGenerator, Optional
from openai import APIError, RateLimitError, AuthenticationError, APITimeoutError, BadRequestError

from ..base import (
    LLMProviderConfig, LLMRunConfig, LLMMessage, LLMEngineCallbacks, 
    LLMToolCall, LLMUsage, LLMResult, LLMEngineError, LLMAuthenticationError, 
    LLMRateLimitError, LLMContextLengthExceededError, LLMBadRequestError
)
from ._base import LLMClientBase
from ..main import register_llm_client

@register_llm_client("openai")
class OpenAIClient(LLMClientBase):
    """使用 'openai' Python SDK 的、经过增强的 LLM 客户端实现。"""
    
    def __init__(self, config: LLMProviderConfig):
        self.config = config
        self.client = openai.AsyncOpenAI(
            api_key=config.api_key,
            base_url=str(config.base_url) if config.base_url else None,
            timeout=config.timeout,
            max_retries=config.max_retries,
        )

    async def _handle_streamed_response(
        self, 
        stream: AsyncGenerator, 
        run_config: LLMRunConfig,
        messages: List[LLMMessage], 
        callbacks: Optional[LLMEngineCallbacks]
    ) -> LLMResult:
        """私有方法，专门处理流式响应。"""
        full_content = ""
        tool_calls_buffer = []
        final_message = LLMMessage(role="assistant")
        final_usage = LLMUsage()

        try:
            async for chunk in stream:
                if chunk.choices:
                    choice = chunk.choices[0]
                    # 1. 处理工具调用
                    tool_calls = choice.delta.tool_calls
                    if tool_calls:
                        for tool_call_chunk in tool_calls:
                            if len(tool_calls_buffer) <= tool_call_chunk.index:
                                tool_calls_buffer.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                            
                            current_call = tool_calls_buffer[tool_call_chunk.index]
                            if tool_call_chunk.id: current_call["id"] = tool_call_chunk.id
                            if tool_call_chunk.function.name: current_call["function"]["name"] = tool_call_chunk.function.name
                            if tool_call_chunk.function.arguments: current_call["function"]["arguments"] += tool_call_chunk.function.arguments

                    # 2. 处理文本块
                    content_chunk = choice.delta.content
                    if content_chunk:
                        full_content += content_chunk
                        if callbacks: await callbacks.on_chunk_generated(content_chunk)
                
                    # 3. 检查结束原因
                    finish_reason = choice.finish_reason
                    if finish_reason == "tool_calls":
                        parsed_tool_calls = [LLMToolCall(**tc) for tc in tool_calls_buffer]
                        if callbacks: await callbacks.on_tool_calls_generated(parsed_tool_calls)
                        final_message.tool_calls = tool_calls_buffer
                    elif finish_reason == "stop":
                        final_message.content = full_content

                # 4. 报告用量 (只有在最新版openai库中，最后一个chunk才有)
                if hasattr(chunk, 'usage') and chunk.usage:
                    final_usage = LLMUsage(**chunk.usage.model_dump())
                    if callbacks: await callbacks.on_usage(final_usage)

            # 返回最终的 LLMResult
            return LLMResult(message=final_message, usage=final_usage)

        except Exception as e:
            # === 统一兜底逻辑 ===
            if callbacks: 
                # 1. 补全 Usage (引擎层最知道怎么算)
                if final_usage.total_tokens == 0:
                    generated_so_far = full_content + json.dumps(tool_calls_buffer)
                    final_usage = self._estimate_usage(
                        self.config.client_name, run_config.model, messages, generated_so_far
                    )
                    # 触发 on_usage 确保 Side Channel (Accumulator) 被更新
                    await callbacks.on_usage(final_usage)

                # 2. 构造 Partial Result
                partial_message = LLMMessage(role="assistant", content=full_content, tool_calls=tool_calls_buffer or None)
                
                partial_result = LLMResult(message=partial_message, usage=final_usage)

                # 3. 触发 on_cancel 作为统一的 "临终遗言" 出口
                # 无论是因为 CancelledError 还是 ConnectionResetError
                await callbacks.on_cancel(partial_result)

            # 4. 重新抛出异常，让上层感知错误类型 (Service 层据此判断是 Cancel 还是 Error)
            raise e
            
    async def _handle_non_streamed_response(
        self, 
        response, 
        run_config: LLMRunConfig,
        messages: List[LLMMessage], 
        callbacks: Optional[LLMEngineCallbacks]
    ) -> LLMResult:
        """私有方法，专门处理非流式响应。"""
        final_message = LLMMessage(role="assistant")
        final_usage = LLMUsage()
        try:
            if response.choices:
                choice = response.choices[0]
                message = choice.message

                if message.tool_calls:
                    parsed_tool_calls = [
                        LLMToolCall(id=tc.id, type=tc.type, function={"name": tc.function.name, "arguments": tc.function.arguments}) 
                        for tc in message.tool_calls
                    ]
                    if callbacks: await callbacks.on_tool_calls_generated(parsed_tool_calls)
                    final_message.tool_calls = [tc.model_dump() for tc in message.tool_calls]
                    
                if message.content:
                    if callbacks: await callbacks.on_chunk_generated(message.content)
                    final_message.content = message.content

            if hasattr(response, 'usage') and response.usage:
                final_usage = LLMUsage(**response.usage.model_dump())
                if callbacks: await callbacks.on_usage(final_usage)

            return LLMResult(message=final_message, usage=final_usage)
        except Exception as e:
            if callbacks:
                if final_usage.total_tokens == 0:
                    estimate_content = ""
                    final_usage = self._estimate_usage(
                        self.config.client_name, run_config.model, messages, estimate_content
                    )
                    await callbacks.on_usage(final_usage)
                partial_result = LLMResult(message=final_message, usage=final_usage)
                await callbacks.on_cancel(partial_result)
            raise e

    async def generate(
        self,
        run_config: LLMRunConfig,
        messages: List[LLMMessage],
        callbacks: Optional[LLMEngineCallbacks] = None,
    ) -> LLMResult:
        api_params = {
            "model": run_config.model,
            "messages": self._messages_to_dict(messages),
            "temperature": run_config.temperature,
            "top_p": run_config.top_p,
            "presence_penalty": run_config.presence_penalty,
            "frequency_penalty": run_config.frequency_penalty,
            "max_tokens": run_config.max_tokens,
            "stream": run_config.stream,
        }

        if run_config.stream:
            api_params["stream_options"] = {"include_usage": True}

        if run_config.response_format:
            api_params["response_format"] = run_config.response_format

        tools = self._tools_to_dict(run_config.tools)

        if tools:
            api_params["tools"] = tools
            api_params["tool_choice"] = run_config.tool_choice

        extra_body = {}

        if run_config.enable_thinking == True and run_config.thinking_budget > 0:
            extra_body["enable_thinking"] = run_config.enable_thinking
            extra_body["thinking_budget"] = run_config.thinking_budget

        try:
            response = await self.client.chat.completions.create(**api_params, extra_body=extra_body)
            
            if run_config.stream:
                return await self._handle_streamed_response(response, run_config, messages, callbacks)
            else:
                return await self._handle_non_streamed_response(response, run_config, messages, callbacks)

        except asyncio.CancelledError:
            raise
        except AuthenticationError as e:
            raise LLMAuthenticationError(f"OpenAI authentication failed: {e.message}")
        except RateLimitError as e:
            raise LLMRateLimitError(f"OpenAI rate limit exceeded: {e.message}")
        except BadRequestError as e:
            if "context_length_exceeded" in e.code:
                raise LLMContextLengthExceededError(f"Context length exceeded for model {run_config.model}: {e.message}")
            raise LLMBadRequestError(f"Invalid request to OpenAI: {e.message}")
        except (APIError, APITimeoutError) as e:
            raise LLMEngineError(f"OpenAI API error: {e.message}")
        except Exception as e:
            # 捕获任何其他意外错误
            raise LLMEngineError(f"An unexpected error occurred in OpenAIClient: {str(e)}")