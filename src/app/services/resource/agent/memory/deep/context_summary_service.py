# src/app/services/resource/agent/memory/deep/context_summary_service.py

import logging
from typing import List, Optional

from sqlalchemy import func

from app.core.context import AppContext
from app.services.base_service import BaseService
from app.services.common.llm_capability_provider import AICapabilityProvider, UsageAccumulator, LLMBillingCallbacks
from app.models import AgentContextSummary
from app.models.resource.agent import AgentMessage, AgentMessageRole
from app.dao.workspace.workspace_dao import WorkspaceDao
from app.dao.resource.agent.agent_memory_dao import AgentContextSummaryDao
from app.dao.resource.resource_dao import ResourceInstanceDao
from app.models.resource.agent.agent_memory import SummaryScope
from app.schemas.resource.agent.agent_schemas import DeepMemoryConfig
from app.schemas.resource.agent.agent_memory_schemas import AgentContextSummaryRead
from app.services.module.service_module_service import ServiceModuleService
from app.services.resource.agent.message_content import agent_message_to_text
from app.engine.model.llm import (
    LLMRunConfig, 
    LLMMessage, 
    LLMResult
)
from app.services.exceptions import ConfigurationError, NotFoundError

logger = logging.getLogger(__name__)

class ContextSummaryService(BaseService):
    """
    [Deep Memory Layer 2] 上下文摘要管理服务。
    负责摘要的存储、检索、归档和删除。
    """
    def __init__(self, context: AppContext):
        self.context = context
        self.dao = AgentContextSummaryDao(context.db)
        self.workspace_dao = WorkspaceDao(context.db)
        self.instance_dao = ResourceInstanceDao(context.db)
        self.module_service = ServiceModuleService(context)
        self.ai_provider = AICapabilityProvider(context)

    async def create_summary_internal(
        self, 
        agent_instance_id: int,
        user_id: int,
        run_id: str,
        turn_id: str,
        content: str,
        module_version_id: int,
        scope: SummaryScope = SummaryScope.SESSION,
        session_uuid: Optional[str] = None,
        trace_id: Optional[str] = None,
        ref_created_at=None,
    ) -> AgentContextSummary:
        """
        [Internal] 供后台 Worker 调用，用于保存生成的摘要。
        """
        summary = AgentContextSummary(
            agent_instance_id=agent_instance_id,
            user_id=user_id,
            run_id=run_id,
            turn_id=turn_id,
            trace_id=trace_id,
            content=content,
            module_version_id=module_version_id,
            scope=scope,
            session_uuid=session_uuid,
            ref_created_at=ref_created_at,
        )
        await self.dao.add(summary)
        return summary

    async def list_summaries(
        self, 
        agent_instance_id: int, 
        user_id: int, 
        session_uuid: Optional[str] = None,
        exclude_turn_ids: Optional[List[str]] = None,
        page: int = 1,
        limit: int = 10
    ) -> List[AgentContextSummaryRead]:
        """
        [UI/Debug] 获取摘要列表，用于可视化面板展示。
        """
        summaries = await self.dao.get_active_summaries(agent_instance_id, user_id, session_uuid, exclude_turn_ids, page, limit)
        return [AgentContextSummaryRead.model_validate(s) for s in summaries]

    async def get_summary(self, summary_uuid: str) -> AgentContextSummaryRead:
        summary = await self.dao.get_by_uuid(summary_uuid)
        if not summary:
            raise NotFoundError("Context summary not found.")
        return AgentContextSummaryRead.model_validate(summary)

    async def invalid_summary_for_turn(
        self,
        turn_id: str,
        *,
        session_uuid: Optional[str] = None,
        agent_instance_id: Optional[int] = None,
        user_id: Optional[int] = None,
        mode: str = "production",
    ):
        """
        [联动接口] 当 AgentSessionService 删除消息时调用。
        作废该业务轮次对应的所有摘要。
        """
        if not turn_id:
            return
        
        if mode == "debug":
            await self.dao.physical_delete_by_turn_id(
                turn_id,
                session_uuid=session_uuid,
                agent_instance_id=agent_instance_id,
                user_id=user_id,
            )
        else:
            await self.dao.soft_delete_by_turn_id(
                turn_id,
                session_uuid=session_uuid,
                agent_instance_id=agent_instance_id,
                user_id=user_id,
            )

    async def delete_session_summaries_physical(self, session_uuid: str):
        await self.dao.physical_delete_by_session_uuid(session_uuid)
        
    async def archive_session_summaries(self, session_uuid: str):
        """[联动接口] 当会话被删除/归档时调用"""
        await self.dao.soft_delete_by_session_uuid(session_uuid)

    async def delete_summary(self, summary_uuid: str, actor_id: int):
        """
        [UI] 用户手动删除某条不准确的摘要。
        """
        summary = await self.dao.get_by_uuid(summary_uuid)
        if not summary:
            raise NotFoundError("Context summary not found.")
        
        # 简单权限检查：只能删除属于自己的摘要
        if summary.user_id != actor_id:
            raise NotFoundError("Context summary not found.")
            
        await self.dao.delete(summary)

    # --- LLM Summarization Logic ---

    def _build_turn_transcript(self, messages: List[AgentMessage]) -> str:
        """
        将对话轮次转换为适合做摘要的 Transcript 格式。
        """
        buffer = []
        for msg in messages:
            role = msg.role.value.capitalize()
            content = agent_message_to_text(msg)
            
            # 简化 Tool Output，避免过多干扰摘要
            if msg.role == AgentMessageRole.TOOL:
                content = f"[Tool Result] (Length: {len(content)})"
            
            buffer.append(f"{role}: {content}")
        
        return "\n".join(buffer)

    async def summarize_turn_background(
        self,
        agent_instance_id: int,
        session_uuid: str,
        run_id: str,
        turn_id: str,
        messages: List[AgentMessage],
        deep_memory_config: DeepMemoryConfig,
        # 显式传入运行时工作空间ID，用于计费归属
        runtime_workspace_id: int,
        trace_id: Optional[str] = None,
    ):
        """
        [Worker Task] 后台任务：生成摘要并入库。
        """
        if not deep_memory_config.enable_summarization or not messages:
            return

        try:
            async with self.ai_provider:
                # 1. 准备上下文 (Agent, Workspace)
                agent_instance = await self.instance_dao.get_by_pk(agent_instance_id)
                if not agent_instance: return

                runtime_workspace = await self.workspace_dao.get_by_pk(runtime_workspace_id)
                if not runtime_workspace:
                    logger.error(f"Runtime workspace {runtime_workspace_id} not found. Aborting summary task.")
                    return

                # 2. 解析模型版本 (使用 Provider 的标准解析逻辑)
                # 自动处理：指定ID -> 校验 -> 回退默认 -> 报错
                target_version = await self.ai_provider.resolve_model_version(
                    deep_memory_config.summary_model_uuid
                )
                
                # 获取执行所需的凭证上下文
                module_context = await self.module_service.get_runtime_context(
                    version_id=target_version.id,
                    actor=self.context.actor,
                    workspace=runtime_workspace
                )

                # 3. 准备 Prompt & Messages
                transcript = self._build_turn_transcript(messages)
                system_prompt = (
                    "You are an expert conversation summarizer. "
                    "Your goal is to create a concise, objective summary of the user's intent and the assistant's key actions or answers. "
                    "Ignore specific tool output details unless they are crucial to the final answer. "
                    "The summary should be 1-2 sentences long."
                )
                llm_messages = [
                    LLMMessage(role="system", content=system_prompt),
                    LLMMessage(role="user", content=f"Conversation Transcript:\n{transcript}\n\nSummary:")
                ]

                # 4. 准备执行配置
                run_config = LLMRunConfig(
                    model=module_context.version.name,
                    temperature=0.3, 
                    max_tokens=200,
                    stream=False
                )
                
                # 5. 初始化用量累加器和回调
                usage_accumulator = UsageAccumulator()
                # [Fix] 将 accumulator 注入回调，确保 Engine 产生的数据能被捕获
                callbacks = LLMBillingCallbacks(usage_accumulator)

                # 6. [核心] 执行并计费
                final_result: Optional[LLMResult] = None
                try:
                    final_result = await self.ai_provider.execute_llm_with_billing(
                        runtime_workspace=runtime_workspace,
                        module_context=module_context,
                        run_config=run_config,
                        messages=llm_messages,
                        callbacks=callbacks,
                        usage_accumulator=usage_accumulator
                    )
                except Exception as e:
                    final_result = callbacks.final_result
                    if not final_result: raise
                
                # 7. 处理结果 & 入库
                summary_content = final_result.message.content.strip()
                if not summary_content:
                    logger.warning("Empty summary generated for turn %s", turn_id)
                    return

                scope_enum = SummaryScope(deep_memory_config.summary_scope)
                
                turn_start_time = messages[0].created_at if messages else func.now()

                await self.invalid_summary_for_turn(
                    turn_id=turn_id,
                    session_uuid=session_uuid,
                    agent_instance_id=agent_instance.id,
                    user_id=self.context.actor.id,
                    mode="production",
                )
                
                await self.create_summary_internal(
                    agent_instance_id=agent_instance.id,
                    user_id=self.context.actor.id,
                    run_id=run_id,
                    turn_id=turn_id,
                    content=summary_content,
                    module_version_id=target_version.id,
                    scope=scope_enum,
                    session_uuid=session_uuid,
                    trace_id=trace_id,
                    ref_created_at=turn_start_time
                )
                
                logger.info("Generated summary for turn %s. Scope: %s. Tokens: %s", turn_id, scope_enum.value, usage_accumulator.total_tokens)

        except Exception as e:
            logger.error("Context summary task failed for turn %s: %s", turn_id, e, exc_info=True)
            raise
