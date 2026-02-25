# src/app/engine/agent/main.py

import asyncio
import logging
import json
from typing import List, Optional

from .base import (
    AgentInput, AgentResult, AgentStep, AgentEngineCallbacks, 
    BaseToolExecutor
)
from ..model.llm import LLMEngineService, LLMProviderConfig, LLMRunConfig, LLMMessage, LLMToolCall, LLMUsage, LLMResult, LLMEngineCallbacks

class AgentEngineService:
    """
    一个有状态的、可插拔的 Agent 引擎，负责编排 LLM、工具和上下文。
    """
    def __init__(
        self,
        # [可选] 传入 (为了测试和高级配置)
        llm_engine: Optional[LLMEngineService] = None,
        max_iterations: int = 5
    ):
        self.llm_engine = llm_engine or LLMEngineService()
        self.max_iterations = max_iterations

    async def run(
        self,
        agent_input: AgentInput,
        # LLM 相关的配置需要透传
        provider_config: LLMProviderConfig,
        run_config: LLMRunConfig,
        tool_executor: BaseToolExecutor,
        callbacks: Optional[AgentEngineCallbacks] = None,
    ) -> AgentResult:
        
        try:
            # --- 步骤 1: 上下文准备 ---
            if callbacks: await callbacks.on_agent_start()
            
            message_history = agent_input.messages.copy()
            intermediate_steps = []

            # 初始化总用量计数器
            total_usage = LLMUsage()

            # --- 步骤 2: 启动 ReAct 循环 ---
            for _ in range(self.max_iterations):

                # --- 内部 LLM 回调处理器 ---
                # 这个回调类现在只负责"透传"流式状态给上层 UI，不再负责控制流逻辑
                class _InternalLLMCallbacks(LLMEngineCallbacks):
                    async def on_chunk_generated(self, chunk: str):
                        # 流式输出最终答案
                        if callbacks: await callbacks.on_final_chunk_generated(chunk)
                    
                    async def on_tool_calls_generated(self, tool_calls: List[LLMToolCall]):
                        # 通知 UI 模型正在请求工具
                        if callbacks: await callbacks.on_tool_calls_generated(tool_calls)

                    async def on_cancel(self, result: LLMResult):
                        # 处理取消事件
                        if callbacks:
                            if result.usage:
                                total_usage.prompt_tokens += result.usage.prompt_tokens
                                total_usage.completion_tokens += result.usage.completion_tokens
                                total_usage.total_tokens += result.usage.total_tokens
                            agent_result = AgentResult(
                                message=result.message,
                                steps=intermediate_steps,
                                usage=total_usage # 返回累加后的总用量
                            )                            
                            await callbacks.on_agent_cancel(agent_result)

                    async def on_usage(self, usage: LLMUsage):
                        # 可选：通知 UI 单次 LLM 调用的消耗
                        if callbacks: await callbacks.on_usage(usage)

                    # 其他回调保持为空或做简单日志
                    async def on_start(self): pass
                    async def on_success(self, result: LLMResult): pass
                    async def on_error(self, error: Exception): pass

                try:
                    # 调用纯粹的 LLM 引擎
                    llm_result: LLMResult = await self.llm_engine.run(
                        provider_config=provider_config,
                        run_config=run_config,
                        messages=message_history,
                        callbacks=_InternalLLMCallbacks()
                    )

                except asyncio.CancelledError:
                    # [关键] 捕获 LLM 层的取消
                    raise # 直接向外抛出，中断循环

                # 累加 Token 用量
                if llm_result.usage:
                    total_usage.prompt_tokens += llm_result.usage.prompt_tokens
                    total_usage.completion_tokens += llm_result.usage.completion_tokens
                    total_usage.total_tokens += llm_result.usage.total_tokens

                # --- 步骤 3: 决策与行动 ---
                assistant_msg = llm_result.message
                
                # 将 Assistant 的回复加入历史
                message_history.append(assistant_msg)

                # 检查是否包含工具调用
                # LLMMessage.tool_calls 是 List[Dict]
                if assistant_msg.tool_calls:
                    # 转换字典列表为 Pydantic 对象列表，方便处理
                    tool_calls_objects = [LLMToolCall(**tc) for tc in assistant_msg.tool_calls]
                    
                    # --- 并行工具执行 ---
                    tasks = []
                    for tool_call in tool_calls_objects:
                        async def execute_tool_task(tc: LLMToolCall):
                            tool_name = tc.function['name']
                            arguments_str = tc.function.get('arguments', '{}')
                            try:
                                tool_args = json.loads(arguments_str)
                            except json.JSONDecodeError:
                                return tc, {"error": "Invalid JSON arguments provided."}
                            
                            # 执行工具
                            observation = await tool_executor.execute(tool_name, tool_args)
                            return tc, observation

                        tasks.append(execute_tool_task(tool_call))

                    # 并发执行所有工具
                    tool_results = await asyncio.gather(*tasks, return_exceptions=True)

                    # 处理结果并更新历史
                    for i, result in enumerate(tool_results):
                        original_tool_call = tool_calls_objects[i]
                        
                        if isinstance(result, Exception):
                            observation = f'{{"error": "Tool execution failed unexpectedly.", "details": "{str(result)}"}}'
                        else:
                            _, observation = result

                        # 记录步骤
                        step = AgentStep(action=original_tool_call, observation=observation)
                        intermediate_steps.append(step)
                        if callbacks: await callbacks.on_agent_step(step)
                        
                        # 更新消息历史
                        message_history.append(LLMMessage(
                            role="tool",
                            tool_call_id=original_tool_call.id,
                            content=json.dumps(observation, ensure_ascii=False)
                        ))
                    
                    # 完成工具调用后，continue 进入下一轮循环，将工具结果发回给 LLM
                    continue 
                
                else: 
                    # --- 结束条件 ---
                    # 如果模型没有调用工具，说明生成了最终答案
                    result = AgentResult(
                        message=assistant_msg,
                        steps=intermediate_steps,
                        usage=total_usage # 返回累加后的总用量
                    )
                    if callbacks: await callbacks.on_agent_finish(result)
                    return result

            # 如果循环结束还没有返回最终答案
            raise Exception("Agent reached maximum iterations without a final answer.")

        except asyncio.CancelledError:
            # [关键] 整个 Agent 任务被取消
            logging.info("Agent execution cancelled.")
            raise # 继续向上抛出给 Service 层

        except Exception as e:
            if callbacks: await callbacks.on_agent_error(e)
            raise