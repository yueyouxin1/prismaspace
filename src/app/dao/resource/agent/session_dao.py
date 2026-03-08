# src/app/dao/resource/agent/session_dao.py

from typing import List, Optional
from sqlalchemy import select, desc, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.dao.base_dao import BaseDao
from app.models.resource.agent.session import AgentSession, AgentMessage

class AgentSessionDao(BaseDao[AgentSession]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(AgentSession, db_session)

    async def get_by_uuid(self, uuid: str, withs: list = None) -> Optional[AgentSession]:
        return await self.get_one(where={"uuid": uuid}, withs=withs)

    async def list_by_user_and_agent(self, user_id: int, agent_instance_id: int, page: int, limit: int) -> List[AgentSession]:
        """获取用户在特定 Agent 实例下的会话列表，排除已归档的"""
        return await self.get_list(
            where={
                "user_id": user_id, 
                "agent_instance_id": agent_instance_id,
                "is_archived": False
            },
            order=[desc(AgentSession.updated_at)],
            page=page,
            limit=limit
        )

class AgentMessageDao(BaseDao[AgentMessage]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(AgentMessage, db_session)

    async def get_by_uuid(self, uuid: str, withs: list = None) -> Optional[AgentMessage]:
        return await self.get_one(where={"uuid": uuid}, withs=withs)

    async def get_active_count(self, session_id: int) -> int:
        """
        [Source of Truth] 获取当前会话的有效消息总数。
        严谨过滤掉 is_deleted=True 的消息。
        """
        stmt = (
            select(func.count())
            .select_from(AgentMessage)
            .where(
                AgentMessage.session_id == session_id,
                AgentMessage.is_deleted == False  # 核心：排除软删除
            )
        )
        result = await self.db_session.execute(stmt)
        return result.scalar() or 0

    async def get_active_turn_count(self, session_id: int) -> int:
        stmt = (
            select(func.count(func.distinct(AgentMessage.turn_id)))
            .select_from(AgentMessage)
            .where(
                AgentMessage.session_id == session_id,
                AgentMessage.is_deleted == False,
                AgentMessage.turn_id.is_not(None),
            )
        )
        result = await self.db_session.execute(stmt)
        return result.scalar() or 0

    async def get_existing_turn_ids(self, session_id: int, turn_ids: list[str]) -> set[str]:
        normalized_turn_ids = [turn_id for turn_id in turn_ids if isinstance(turn_id, str) and turn_id]
        if not normalized_turn_ids:
            return set()

        stmt = (
            select(AgentMessage.turn_id)
            .where(
                AgentMessage.session_id == session_id,
                AgentMessage.is_deleted == False,
                AgentMessage.turn_id.in_(normalized_turn_ids),
            )
            .group_by(AgentMessage.turn_id)
        )
        result = await self.db_session.execute(stmt)
        return {
            turn_id
            for turn_id in result.scalars().all()
            if isinstance(turn_id, str) and turn_id
        }

    async def get_active_message_count_for_turn(self, session_id: int, turn_id: str) -> int:
        if not isinstance(turn_id, str) or not turn_id:
            return 0

        stmt = (
            select(func.count())
            .select_from(AgentMessage)
            .where(
                AgentMessage.session_id == session_id,
                AgentMessage.is_deleted == False,
                AgentMessage.turn_id == turn_id,
            )
        )
        result = await self.db_session.execute(stmt)
        return result.scalar() or 0

    async def get_active_messages_for_turn_scope(
        self,
        *,
        turn_id: str,
        session_uuid: str,
        agent_instance_id: int,
    ) -> List[AgentMessage]:
        if not isinstance(turn_id, str) or not turn_id:
            return []
        if not isinstance(session_uuid, str) or not session_uuid:
            return []

        stmt = (
            select(AgentMessage)
            .join(AgentSession, AgentSession.id == AgentMessage.session_id)
            .where(
                AgentMessage.turn_id == turn_id,
                AgentMessage.is_deleted == False,
                AgentSession.uuid == session_uuid,
                AgentSession.agent_instance_id == agent_instance_id,
            )
            .order_by(AgentMessage.created_at.asc(), AgentMessage.id.asc())
        )
        result = await self.db_session.execute(stmt)
        return list(result.scalars().all())
        
    async def get_active_messages(self, session_id: int, page: int = 0, limit: int = 0) -> List[AgentMessage]:
        """
        获取活跃的历史消息 (用于构建 LLM Context)。
        [关键] 必须排除 is_deleted=True 的消息。
        """
        stmt = (
            select(AgentMessage)
            .where(
                AgentMessage.session_id == session_id,
                AgentMessage.is_deleted == False # 核心过滤
            )
            .order_by(desc(AgentMessage.id)) # 先倒序取最近的
            .limit(limit)
        )
        if page > 0 and limit > 0:
            stmt = self._paginate(stmt=stmt, page=page, limit=limit)
        result = await self.db_session.execute(stmt)
        messages = list(result.scalars().all())
        messages.reverse() # 转回正序 (时间顺序)
        return messages

    async def get_messages_by_turns(self, session_id: int, turns: int) -> List[AgentMessage]:
        """
        [Smart Retrieval] 获取最近 N 轮（基于 turn_id 分组）的消息。
        """
        # 1. 子查询：找出最近的 N 个 turn_id
        subquery = (
            select(AgentMessage.turn_id)
            .where(
                AgentMessage.session_id == session_id,
                AgentMessage.is_deleted == False,
                AgentMessage.turn_id.is_not(None)
            )
            .group_by(AgentMessage.turn_id)
            .order_by(func.max(AgentMessage.id).desc())
            .limit(turns)
        ).scalar_subquery()
        
        stmt = (
            select(AgentMessage)
            .where(
                AgentMessage.session_id == session_id,
                AgentMessage.is_deleted == False,
                AgentMessage.turn_id.in_(subquery)
            )
            .order_by(AgentMessage.id.asc())
        )
        
        result = await self.db_session.execute(stmt)
        return list(result.scalars().all())
        
    async def get_history_for_frontend(self, session_id: int, cursor_id: int = 0, limit: int = 20) -> List[AgentMessage]:
        """获取前端展示用的历史记录，包含已软删除的(如果前端需要展示删除标记)？通常不需要展示已清除的。"""
        # 这里的逻辑取决于业务：清空上下文后，用户还要看到历史吗？
        # 定义中“清空”意味着“屏幕清空”。所以前端也不应该拉取到。
        where = [
            AgentMessage.session_id == session_id,
            AgentMessage.is_deleted == False
        ]
        if cursor_id > 0:
            where.append(AgentMessage.id < cursor_id)
            
        return await self.get_list(
            where=where,
            order=[desc(AgentMessage.id)],
            limit=limit
        )

    async def soft_delete_by_session(self, session_id: int):
        """[生产模式] 软删除会话下的所有消息"""
        stmt = (
            update(AgentMessage)
            .where(AgentMessage.session_id == session_id)
            .values(is_deleted=True)
        )
        await self.db_session.execute(stmt)

    async def physical_delete_by_session(self, session_id: int):
        """[调试模式] 物理删除会话下的所有消息"""
        stmt = (
            delete(AgentMessage)
            .where(AgentMessage.session_id == session_id)
        )
        await self.db_session.execute(stmt)
