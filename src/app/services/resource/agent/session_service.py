# src/app/services/resource/agent/session_service.py

from typing import List, Optional, Dict, Any
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.context import AppContext
from app.models import User, ResourceInstance
from app.models.interaction.chat import ChatSession, ChatMessage, MessageRole
from app.dao.interaction.chat_dao import ChatSessionDao, ChatMessageDao
from app.dao.resource.resource_dao import ResourceInstanceDao
from app.services.resource.agent.memory.deep.context_summary_service import ContextSummaryService
from app.schemas.interaction.chat_schemas import ChatSessionCreate, ChatSessionRead, ChatMessageRead
from app.engine.model.llm import LLMMessage
from app.services.base_service import BaseService
from app.services.exceptions import NotFoundError, PermissionDeniedError, ServiceException

class SessionService(BaseService):
    """
    [Core Service] 负责管理 Agent 会话及其上下文生命周期。
    """
    def __init__(self, context: AppContext):
        self.context = context
        self.db = context.db
        self.session_dao = ChatSessionDao(context.db)
        self.message_dao = ChatMessageDao(context.db)
        self.instance_dao = ResourceInstanceDao(context.db)
        self.summary_service = ContextSummaryService(context)

    async def _sync_message_count(self, session: ChatSession) -> int:
        """
        [Internal] 强制同步数据库行数到 Session 实体缓存字段。
        返回最新的真实数量。
        """
        real_count = await self.message_dao.get_active_count(session.id)
        
        # 只有当数量不一致时才触发 DB 更新，减少无谓写操作
        if session.message_count != real_count:
            session.message_count = real_count
        
        return real_count

    async def create_session(self, create_data: ChatSessionCreate, actor: User) -> ChatSessionRead:
        """用户创建一个新的会话"""
        instance = await self.instance_dao.get_by_uuid(create_data.agent_instance_uuid)
        if not instance:
            raise NotFoundError("Agent instance not found.")
        
        # 权限检查：用户是否有权访问该 Agent
        # 简化逻辑：如果是公开的或工作区内的成员
        workspace = instance.resource.workspace
        await self.context.perm_evaluator.ensure_can(["resource:execute"], target=workspace)

        session = ChatSession(
            user_id=actor.id,
            agent_instance_id=instance.id,
            title=create_data.title or "New Chat"
        )
        await self.session_dao.add(session)
        
        # 重新加载以填充 agent_instance 关系
        fresh_session = await self.session_dao.get_one(where={"id": session.id}, withs=["agent_instance"])
        return ChatSessionRead.model_validate(fresh_session)

    async def list_sessions(self, agent_instance_uuid: str, page: int, limit: int, actor: User) -> List[ChatSessionRead]:
        """列出当前用户在某个 Agent 版本下的历史会话"""
        instance = await self.instance_dao.get_by_uuid(agent_instance_uuid)
        if not instance:
            raise NotFoundError("Agent instance not found.")
            
        sessions = await self.session_dao.list_by_user_and_agent(
            user_id=actor.id, 
            agent_instance_id=instance.id, 
            page=page, 
            limit=limit
        )
        return [ChatSessionRead.model_validate(s) for s in sessions]

    async def get_session(self, session_uuid: str, actor: User) -> ChatSession:
        """[Internal] 获取并鉴权会话实体"""
        session = await self.session_dao.get_by_uuid(session_uuid)
        if not session:
            raise NotFoundError("Chat session not found")
        if session.user_id != actor.id:
            raise PermissionDeniedError("Access denied to this session")
        if session.is_archived:
            raise ServiceException("This session has been archived.")
        return session

    async def get_session_history(self, session_uuid: str, cursor: int, limit: int, actor: User) -> List[ChatMessageRead]:
        """获取会话的消息历史 (用于前端展示)"""
        session = await self.get_session(session_uuid, actor)
        messages = await self.message_dao.get_history_for_frontend(session.id, cursor, limit)
        return [ChatMessageRead.model_validate(m) for m in messages]

    async def append_message(
        self, 
        session: ChatSession, 
        role: MessageRole, 
        content: str = None, 
        tool_calls: List[Dict[str, Any]] = None, 
        tool_call_id: str = None,
        trace_id: str = None,
        token_count: int = 0
    ) -> ChatMessage:
        """
        [Atomic] 持久化消息。
        """
        msg = ChatMessage(
            session_id=session.id,
            role=role,
            content=content,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            token_count=token_count,
            trace_id=trace_id
        )
        await self.db.add(msg)

        session.message_count = ChatSession.message_count + 1
        
        session.updated_at = func.now()

        await self.db.flush()
        
        return msg

    async def batch_append_messages(
            self, 
            session: ChatSession, 
            messages_data: List[Dict[str, Any]]
        ):
            """
            [Optimization] 批量插入消息，减少 DB 往返。
            """
            if not messages_data:
                return

            orm_messages = []
            for data in messages_data:
                msg = ChatMessage(
                    session_id=session.id,
                    role=data['role'],
                    content=data.get('content'),
                    tool_calls=data.get('tool_calls'),
                    tool_call_id=data.get('tool_call_id'),
                    token_count=data.get('token_count', 0),
                    trace_id=data.get('trace_id')
                )
                orm_messages.append(msg)
            
            self.db.add_all(orm_messages)
            session.message_count = ChatSession.message_count + len(orm_messages)
            session.updated_at = func.now()
            
            await self.db.flush()

    async def clear_context(self, session_uuid: str, mode: str, actor: User):
        """
        清空上下文：
        1. 软/硬删除消息
        2. 同步软/硬删除该会话产生的所有摘要
        """
        session = await self.get_session(session_uuid, actor)
        
        # 1. 处理消息
        if mode == "debug":
            await self.message_dao.physical_delete_by_session(session.id)
            # 2. 处理摘要 (物理删除)
            await self.summary_service.delete_session_summaries_physical(session.uuid)
        else:
            await self.message_dao.soft_delete_by_session(session.id)
            # 2. 处理摘要 (软删除/归档)
            await self.summary_service.archive_session_summaries(session.uuid)
        
        # 3. 重置计数器 (配合之前的锯齿优化)
        session.message_count = 0
        await self.db.flush()

    async def delete_message(self, message_uuid: str, actor: User, mode: str = "production"):
        """
        [新功能] 删除单条消息，并级联处理摘要。
        """
        # 1. 获取消息 (包含鉴权)
        msg = await self.message_dao.get_by_uuid(message_uuid, withs=["session"])
        if not msg:
            raise NotFoundError("Message not found")
        
        # 简单鉴权：检查 Session 归属
        if msg.session.user_id != actor.id:
            raise PermissionDeniedError("Cannot delete message from this session")

        # 2. 捕获关键信息用于级联
        target_trace_id = msg.trace_id
        
        # 3. 删除消息
        if mode == "debug":
            await self.message_dao.physical_delete(msg)
        else:
            msg.is_deleted = True
            # msg.deleted_at = func.now() # 如果有此字段
        
        # 4. [级联操作] Summary 共生死
        # 只要 Trace 中的任何一条消息被删，该 Trace 的摘要即失效
        if target_trace_id:
            await self.summary_service.invalid_summary_for_trace(
                trace_id=target_trace_id, 
                mode=mode
            )

        session.message_count = ChatSession.message_count - 1  
        await self.db.flush()

    async def delete_session(self, session_uuid: str, actor: User):
        session = await self.get_session(session_uuid, actor)
        session.is_archived = True
        
        # [级联] Session 亡，Summary 亡
        await self.summary_service.archive_session_summaries(session.uuid)
        await self.db.flush()

    async def get_recent_messages(self, session_id: int, limit: int) -> List[ChatMessage]:
        """
        [Raw Data] 仅获取最近 N 条原始数据库消息。
        格式化逻辑移交给 ContextBuilderProcessor。
        """
        return await self.message_dao.get_messages_by_turns(session_id, turns=limit)
