# src/app/services/resource/agent/agent_session_manager.py

import logging
from typing import List, Dict, Any, Optional
from app.core.context import AppContext
from app.models import User, Workspace
from app.models.resource.agent import Agent, AgentMessage, AgentSession, AgentMessageRole
from app.services.resource.agent.session_service import AgentSessionService
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
        run_id: str,
        turn_id: str,
        trace_id: Optional[str],
        agent_instance: Agent,
        # 显式传入运行时工作空间ID，用于计费归属
        runtime_workspace: Workspace,
        actor: User,
        create_if_missing: bool = False,
    ):
        self.context = context
        self.actor = actor
        self.agent_instance = agent_instance
        self.runtime_workspace = runtime_workspace
        self.session_uuid = session_uuid
        self.create_if_missing = create_if_missing
        
        # 依赖服务
        self.session_service = AgentSessionService(context)
        
        # 内部状态
        self.session: Optional[AgentSession] = None
        self.run_id: str = run_id
        self.turn_id: str = turn_id
        self.trace_id: str = trace_id
        self.message_buffer: List[Dict[str, Any]] = [] # 待提交的消息缓冲区
        # 单次请求内的最近 turn 快照缓存，避免首 token 前重复查库。
        self._recent_turn_messages_cache: List[AgentMessage] = []
        self._recent_turn_messages_cache_turns: int = 0
        self._post_commit_jobs: List[Dict[str, Any]] = []

    async def initialize(self):
        """加载或创建会话"""
        if self.session_uuid:
            if self.create_if_missing:
                self.session = await self.session_service.get_or_create_session(
                    session_uuid=self.session_uuid,
                    agent_instance=self.agent_instance,
                    actor=self.actor,
                )
            else:
                self.session = await self.session_service.get_session(self.session_uuid, self.actor)
                if not self.session:
                    raise NotFoundError("Agent Session Not Found.")
            if self.session and self.agent_instance.id != self.session.agent_instance_id:
                self.session = None
                raise ServiceException("Agent Session Initialize Error.")

    @staticmethod
    def _slice_recent_turn_messages(messages: List[AgentMessage], turns: int) -> List[AgentMessage]:
        if turns <= 0 or not messages:
            return []

        selected_turn_ids: List[str] = []
        seen_turn_ids = set()
        for message in reversed(messages):
            turn_id = getattr(message, "turn_id", None)
            if not isinstance(turn_id, str) or not turn_id or turn_id in seen_turn_ids:
                continue
            seen_turn_ids.add(turn_id)
            selected_turn_ids.append(turn_id)
            if len(selected_turn_ids) >= turns:
                break

        if not selected_turn_ids:
            return []

        selected_turn_id_set = set(selected_turn_ids)
        return [
            message
            for message in messages
            if getattr(message, "turn_id", None) in selected_turn_id_set
        ]

    async def get_recent_messages(self, turns: int) -> List[AgentMessage]:
        if not self.session or turns <= 0:
            return []

        if self._recent_turn_messages_cache_turns >= turns and self._recent_turn_messages_cache:
            return self._slice_recent_turn_messages(self._recent_turn_messages_cache, turns)

        recent_messages = await self.session_service.get_recent_messages(self.session.id, limit=turns)
        self._recent_turn_messages_cache = recent_messages
        self._recent_turn_messages_cache_turns = turns
        return recent_messages

    async def preload_recent_messages(self, turns: int) -> None:
        await self.get_recent_messages(turns)

    def buffer_message(
        self, 
        role: AgentMessageRole, 
        message_uuid: Optional[str] = None,
        text_content: Optional[str] = None,
        content_parts: Optional[List[Dict[str, Any]]] = None,
        reasoning_content: Optional[str] = None,
        activity_type: Optional[str] = None,
        encrypted_value: Optional[str] = None,
        content: str = None,
        tool_calls: List[Dict] = None, 
        tool_call_id: str = None,
        token_count: int = 0,
        meta: Optional[Dict[str, Any]] = None,
    ):
        if not self.session:
            # 无状态模式不持久化
            return
        """
        [Buffer] 将消息暂存到内存缓冲区，等待本轮 Trace 结束统一提交。
        """
        normalized_meta = dict(meta or {})

        self.message_buffer.append({
            "message_uuid": message_uuid,
            "role": role,
            "text_content": text_content if text_content is not None else content,
            "content_parts": content_parts,
            "reasoning_content": reasoning_content,
            "activity_type": activity_type,
            "encrypted_value": encrypted_value,
            "content": text_content if text_content is not None else content,
            "tool_calls": tool_calls,
            "tool_call_id": tool_call_id,
            "token_count": token_count,
            "run_id": self.run_id,
            "turn_id": self.turn_id,
            "trace_id": self.trace_id,
            "meta": normalized_meta or None,
        })

    def clear_post_commit_jobs(self) -> None:
        self._post_commit_jobs.clear()

    async def dispatch_post_commit_jobs(self) -> None:
        if not self._post_commit_jobs:
            return

        pending_jobs = list(self._post_commit_jobs)
        self._post_commit_jobs.clear()
        for job in pending_jobs:
            job_name = job["name"]
            payload = dict(job["payload"])
            try:
                await self.context.arq_pool.enqueue_job(job_name, **payload)
                logger.info(
                    "Dispatched deferred job %s for turn %s (run %s)",
                    job_name,
                    payload.get("turn_id"),
                    payload.get("run_id"),
                )
            except Exception as exc:
                logger.error(
                    "Failed to dispatch deferred job %s for turn %s: %s",
                    job_name,
                    payload.get("turn_id"),
                    exc,
                    exc_info=True,
                )

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
            # 1. 批量写入 DB (AgentSessionService)
            await self.session_service.batch_append_messages(
                session=self.session,
                messages_data=self.message_buffer
            )
            # 只有在 DB 写入成功后，才清空 buffer
            messages_committed = list(self.message_buffer) # 浅拷贝用于后续任务
            self.message_buffer.clear() 
            self._recent_turn_messages_cache = []
            self._recent_turn_messages_cache_turns = 0
            # 2. 检查是否有价值内容 (User/Assistant)
            has_valuable_content = any(
                m['role'] in [AgentMessageRole.USER, AgentMessageRole.ASSISTANT] 
                for m in messages_committed
            )
            
            if deep_memory_config.enabled and has_valuable_content:
                # 3. 触发深度记忆后台任务 (根据配置)
                
                # Layer 1: Long Term Context (Vector Indexing)
                # 即使 deep_memory 总开关关闭，如果未来打算开启，现在索引也是有益的？
                # 策略：严格遵循配置。如果 enabled=False，不消耗资源进行索引。
                job_payload = {
                    "agent_instance_id": self.session.agent_instance_id,
                    "session_uuid": self.session.uuid,
                    "run_id": self.run_id,
                    "turn_id": self.turn_id,
                    "trace_id": self.trace_id,
                    "runtime_workspace_id": self.runtime_workspace.id,
                    "user_uuid": self.actor.uuid,
                }

                if deep_memory_config.enable_vector_recall:
                    self._post_commit_jobs.append(
                        {
                            "name": "index_turn_task",
                            "payload": dict(job_payload),
                        }
                    )
                    logger.info(
                        "Queued deferred long-term context indexing for turn %s (run %s)",
                        self.turn_id,
                        self.run_id,
                    )

                # Layer 2: Context Summarization
                if deep_memory_config.enable_summarization:
                    self._post_commit_jobs.append(
                        {
                            "name": "summarize_turn_task",
                            "payload": dict(job_payload),
                        }
                    )
                    logger.info(
                        "Queued deferred context summarization for turn %s (run %s)",
                        self.turn_id,
                        self.run_id,
                    )
            
            logger.info("Committed %s messages for turn %s (run %s)", len(messages_committed), self.turn_id, self.run_id)
            
        except Exception as e:
            logger.error(f"Failed to commit agent session buffer: {e}", exc_info=True)
            raise e # 重新抛出，让 Worker 知道失败了
