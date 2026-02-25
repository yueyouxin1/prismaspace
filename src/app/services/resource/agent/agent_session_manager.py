# src/app/services/resource/agent/agent_session_manager.py

import uuid
import logging
from typing import List, Dict, Any, Optional
from app.core.context import AppContext
from app.models import User, Workspace
from app.models.resource.agent import Agent
from app.models.interaction.chat import ChatSession, MessageRole
from app.services.resource.agent.session_service import SessionService
from app.schemas.resource.agent.agent_schemas import DeepMemoryConfig
from app.services.exceptions import ServiceException, NotFoundError

logger = logging.getLogger(__name__)

class AgentSessionManager:
    """
    [Facade] 统一管理 Agent 会话的生命周期、消息缓冲与持久化。
    
    职责边界：
    1. 会话加载/创建 (Session Lifecycle)
    2. 消息缓冲 (Message Buffer)
    3. 事务性提交与后台任务触发 (Commit & Background Triggers)
    
    注意：上下文构建 (Context Building) 已移交给 ContextBuilderProcessor。
    """
    def __init__(
        self, 
        context: AppContext, 
        session_uuid: Optional[str], 
        trace_id: Optional[str],
        agent_instance: Agent,
        # 显式传入运行时工作空间ID，用于计费归属
        runtime_workspace: Workspace,
        actor: User
    ):
        self.context = context
        self.actor = actor
        self.agent_instance = agent_instance
        self.runtime_workspace = runtime_workspace
        self.session_uuid = session_uuid
        
        # 依赖服务
        self.session_service = SessionService(context)
        
        # 内部状态
        self.session: Optional[ChatSession] = None
        self.trace_id: str = trace_id
        self.message_buffer: List[Dict[str, Any]] = [] # 待提交的消息缓冲区

    async def initialize(self):
        """加载或创建会话"""
        if self.session_uuid:
            self.session = await self.session_service.get_session(self.session_uuid, self.actor)
            if not self.session:
                raise NotFoundError(f"Agent Session Not Found.")
            if self.session and self.agent_instance.id != self.session.agent_instance_id:
                self.session = None
                raise ServiceException(f"Agent Session Initialize Error.")

    def buffer_message(
        self, 
        role: MessageRole, 
        content: str = None, 
        tool_calls: List[Dict] = None, 
        tool_call_id: str = None,
        token_count: int = 0
    ):
        if not self.session:
            # 无状态模式不持久化
            return
        """
        [Buffer] 将消息暂存到内存缓冲区，等待本轮 Trace 结束统一提交。
        """
        self.message_buffer.append({
            "role": role,
            "content": content,
            "tool_calls": tool_calls,
            "tool_call_id": tool_call_id,
            "token_count": token_count,
            "trace_id": self.trace_id
        })

    async def commit(self, deep_memory_config: DeepMemoryConfig):
        """
        [Commit] 事务性地提交缓冲区：写入 DB + 触发深度记忆后台任务。
        
        Args:
            deep_memory_config: 包含深度记忆开关配置，决定是否触发后台索引和摘要任务。
        """
        if not self.session:
            # 无状态模式不持久化
            return

        if not self.message_buffer:
            return

        try:
            # 1. 批量写入 DB (SessionService)
            await self.session_service.batch_append_messages(
                session=self.session,
                messages_data=self.message_buffer
            )
            # 只有在 DB 写入成功后，才清空 buffer
            messages_committed = list(self.message_buffer) # 浅拷贝用于后续任务
            self.message_buffer.clear() 
            # 2. 检查是否有价值内容 (User/Assistant)
            has_valuable_content = any(
                m['role'] in [MessageRole.USER, MessageRole.ASSISTANT] 
                for m in messages_committed
            )
            
            if deep_memory_config.enabled and has_valuable_content:
                # 3. 触发深度记忆后台任务 (根据配置)
                
                # Layer 1: Long Term Context (Vector Indexing)
                # 即使 deep_memory 总开关关闭，如果未来打算开启，现在索引也是有益的？
                # 策略：严格遵循配置。如果 enabled=False，不消耗资源进行索引。
                if deep_memory_config.enable_vector_recall:
                    await self.context.arq_pool.enqueue_job(
                        'index_long_term_context_task',
                        agent_instance_id=self.session.agent_instance_id,
                        session_uuid=self.session.uuid,
                        trace_id=self.trace_id,
                        runtime_workspace_id=self.runtime_workspace.id,
                        user_uuid=self.actor.uuid
                    )
                    logger.info(f"Triggered long-term context indexing for trace {self.trace_id}")

                # Layer 2: Context Summarization
                if deep_memory_config.enable_summarization:
                    await self.context.arq_pool.enqueue_job(
                        'summarize_trace_task',
                        agent_instance_id=self.session.agent_instance_id,
                        session_uuid=self.session.uuid,
                        trace_id=self.trace_id,
                        runtime_workspace_id=self.runtime_workspace.id,
                        user_uuid=self.actor.uuid
                    )
                    logger.info(f"Triggered context summarization for trace {self.trace_id}")
            
            logger.info(f"Committed {len(messages_committed)} messages for trace {self.trace_id}")
            
        except Exception as e:
            logger.error(f"Failed to commit agent session buffer: {e}", exc_info=True)
            raise e # 重新抛出，让 Worker 知道失败了