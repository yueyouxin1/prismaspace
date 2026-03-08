# src/app/services/resource/agent/session_service.py

from typing import List, Optional, Dict, Any
from sqlalchemy import func
from app.core.context import AppContext
from app.models import User, ResourceInstance
from app.models.resource.agent import AgentSession, AgentMessage, AgentMessageRole
from app.dao.resource.agent.session_dao import AgentSessionDao, AgentMessageDao
from app.dao.resource.resource_dao import ResourceInstanceDao
from app.services.resource.agent.memory.deep.context_summary_service import ContextSummaryService
from app.schemas.resource.agent.session_schemas import AgentSessionCreate, AgentSessionRead, AgentMessageRead
from app.services.base_service import BaseService
from app.services.exceptions import NotFoundError, PermissionDeniedError, ServiceException

class AgentSessionService(BaseService):
    """
    [Core Service] 负责管理 Agent 会话及其上下文生命周期。
    """
    def __init__(self, context: AppContext):
        self.context = context
        self.db = context.db
        self.session_dao = AgentSessionDao(context.db)
        self.message_dao = AgentMessageDao(context.db)
        self.instance_dao = ResourceInstanceDao(context.db)
        self.summary_service = ContextSummaryService(context)

    async def create_session(self, create_data: AgentSessionCreate, actor: User) -> AgentSessionRead:
        """用户创建一个新的会话"""
        instance = await self.instance_dao.get_by_uuid(create_data.agent_instance_uuid)
        if not instance:
            raise NotFoundError("Agent instance not found.")
        
        # 权限检查：用户是否有权访问该 Agent
        # 简化逻辑：如果是公开的或工作区内的成员
        workspace = instance.resource.workspace
        await self.context.perm_evaluator.ensure_can(["resource:execute"], target=workspace)

        session = AgentSession(
            user_id=actor.id,
            agent_instance_id=instance.id,
            title=create_data.title or "New Chat"
        )
        await self.session_dao.add(session)

        # 直接构建响应，避免依赖 ORM 关系懒加载/别名路径解析。
        return self._to_session_read(session, create_data.agent_instance_uuid)

    async def list_sessions(self, agent_instance_uuid: str, page: int, limit: int, actor: User) -> List[AgentSessionRead]:
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
        return [self._to_session_read(session, agent_instance_uuid) for session in sessions]

    async def rename_session(self, session_uuid: str, title: str, actor: User) -> AgentSessionRead:
        session = await self.get_session(session_uuid, actor)
        cleaned_title = title.strip()
        if not cleaned_title:
            raise ServiceException("Session title cannot be empty.")
        session.title = cleaned_title
        await self.db.flush()
        instance = await self.instance_dao.get_by_id(session.agent_instance_id)
        instance_uuid = instance.uuid if instance else ""
        return self._to_session_read(session, instance_uuid)

    def _to_session_read(self, session: AgentSession, agent_instance_uuid: str) -> AgentSessionRead:
        return AgentSessionRead.model_validate({
            "uuid": session.uuid,
            "title": session.title,
            "agent_instance_uuid": agent_instance_uuid,
            "message_count": session.message_count,
            "updated_at": session.updated_at,
            "created_at": session.created_at,
        })

    async def get_session(self, session_uuid: str, actor: User) -> AgentSession:
        """[Internal] 获取并鉴权会话实体"""
        session = await self.session_dao.get_by_uuid(session_uuid)
        if not session:
            raise NotFoundError("Agent session not found")
        if session.user_id != actor.id:
            raise PermissionDeniedError("Access denied to this session")
        if session.is_archived:
            raise ServiceException("This session has been archived.")
        return session

    async def get_or_create_session(
        self,
        session_uuid: str,
        agent_instance: ResourceInstance,
        actor: User,
    ) -> AgentSession:
        session = await self.session_dao.get_by_uuid(session_uuid)
        if session:
            if session.user_id != actor.id:
                raise PermissionDeniedError("Access denied to this session")
            if session.is_archived:
                raise ServiceException("This session has been archived.")
            if session.agent_instance_id != agent_instance.id:
                raise ServiceException("Session bound to another agent instance.")
            return session

        created = AgentSession(
            uuid=session_uuid,
            user_id=actor.id,
            agent_instance_id=agent_instance.id,
            title="New Chat",
        )
        await self.session_dao.add(created)
        await self.db.flush()
        return created

    async def get_session_history(self, session_uuid: str, cursor: int, limit: int, actor: User) -> List[AgentMessageRead]:
        """获取会话的消息历史 (用于前端展示)"""
        session = await self.get_session(session_uuid, actor)
        messages = await self.message_dao.get_history_for_frontend(session.id, cursor, limit)
        return [AgentMessageRead.model_validate(m) for m in messages]

    async def append_message(
        self, 
        session: AgentSession, 
        role: AgentMessageRole, 
        message_uuid: Optional[str] = None,
        text_content: str = None,
        content_parts: Optional[List[Dict[str, Any]]] = None,
        reasoning_content: Optional[str] = None,
        activity_type: Optional[str] = None,
        encrypted_value: Optional[str] = None,
        content: str = None,
        tool_calls: List[Dict[str, Any]] = None, 
        tool_call_id: str = None,
        run_id: str = None,
        turn_id: str = None,
        trace_id: str = None,
        token_count: int = 0,
        meta: Optional[Dict[str, Any]] = None,
    ) -> AgentMessage:
        """
        [Atomic] 持久化消息。
        """
        msg = AgentMessage(
            uuid=message_uuid,
            session_id=session.id,
            role=role,
            content=text_content if text_content is not None else content,
            text_content=text_content if text_content is not None else content,
            content_parts=content_parts,
            reasoning_content=reasoning_content,
            activity_type=activity_type,
            encrypted_value=encrypted_value,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            token_count=token_count,
            run_id=run_id,
            turn_id=turn_id,
            trace_id=trace_id,
            meta=meta,
        )
        if turn_id:
            existing_turn_ids = await self.message_dao.get_existing_turn_ids(session.id, [turn_id])
        else:
            existing_turn_ids = set()

        self.db.add(msg)

        session.message_count = AgentSession.message_count + 1
        if turn_id and turn_id not in existing_turn_ids:
            session.turn_count = AgentSession.turn_count + 1
        
        session.updated_at = func.now()

        await self.db.flush()
        
        return msg

    async def batch_append_messages(
            self, 
            session: AgentSession, 
            messages_data: List[Dict[str, Any]]
        ):
            """
            [Optimization] 批量插入消息，减少 DB 往返。
            """
            if not messages_data:
                return

            orm_messages = []
            incoming_turn_ids = {
                turn_id
                for turn_id in (data.get("turn_id") for data in messages_data)
                if isinstance(turn_id, str) and turn_id
            }
            for data in messages_data:
                msg = AgentMessage(
                    uuid=data.get('message_uuid'),
                    session_id=session.id,
                    role=data['role'],
                    content=data.get('text_content') if data.get('text_content') is not None else data.get('content'),
                    text_content=data.get('text_content') if data.get('text_content') is not None else data.get('content'),
                    content_parts=data.get('content_parts'),
                    reasoning_content=data.get('reasoning_content'),
                    activity_type=data.get('activity_type'),
                    encrypted_value=data.get('encrypted_value'),
                    tool_calls=data.get('tool_calls'),
                    tool_call_id=data.get('tool_call_id'),
                    token_count=data.get('token_count', 0),
                    run_id=data.get('run_id'),
                    turn_id=data.get('turn_id'),
                    trace_id=data.get('trace_id'),
                    meta=data.get('meta'),
                )
                orm_messages.append(msg)
            
            if incoming_turn_ids:
                existing_turn_ids = await self.message_dao.get_existing_turn_ids(
                    session.id,
                    list(incoming_turn_ids),
                )
            else:
                existing_turn_ids = set()

            self.db.add_all(orm_messages)
            session.message_count = AgentSession.message_count + len(orm_messages)
            new_turn_count = len(incoming_turn_ids - existing_turn_ids)
            if new_turn_count > 0:
                session.turn_count = AgentSession.turn_count + new_turn_count
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
        session.turn_count = 0
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
        target_turn_id = msg.turn_id
        
        # 3. 删除消息
        if mode == "debug":
            await self.message_dao.physical_delete(msg)
        else:
            msg.is_deleted = True
            # msg.deleted_at = func.now() # 如果有此字段
        
        # 4. [级联操作] Summary 共生死
        # 只要 turn 中的任何一条消息被删，该轮摘要即失效
        if target_turn_id:
            active_messages_in_turn = await self.message_dao.get_active_message_count_for_turn(
                msg.session_id,
                target_turn_id,
            )
            await self.summary_service.invalid_summary_for_turn(
                turn_id=target_turn_id,
                session_uuid=msg.session.uuid,
                agent_instance_id=msg.session.agent_instance_id,
                user_id=msg.session.user_id,
                mode=mode
            )
            if active_messages_in_turn == 1:
                msg.session.turn_count = AgentSession.turn_count - 1

        msg.session.message_count = AgentSession.message_count - 1  
        await self.db.flush()

    async def delete_session(self, session_uuid: str, actor: User):
        session = await self.get_session(session_uuid, actor)
        session.is_archived = True
        
        # [级联] Session 亡，Summary 亡
        await self.summary_service.archive_session_summaries(session.uuid)
        await self.db.flush()

    async def get_recent_messages(self, session_id: int, limit: int) -> List[AgentMessage]:
        """
        [Raw Data] 仅获取最近 N 个业务轮次的原始数据库消息。
        """
        return await self.message_dao.get_messages_by_turns(session_id, turns=limit)
