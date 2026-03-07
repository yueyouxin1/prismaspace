# src/app/dao/interaction/chat_dao.py

from typing import List, Optional
from sqlalchemy import select, desc, update, delete, func
from sqlalchemy.orm import selectinload, joinedload
from sqlalchemy.ext.asyncio import AsyncSession
from app.dao.base_dao import BaseDao
from app.models.interaction.chat import ChatSession, ChatMessage

class ChatSessionDao(BaseDao[ChatSession]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(ChatSession, db_session)

    async def get_by_uuid(self, uuid: str, withs: list = None) -> Optional[ChatSession]:
        return await self.get_one(where={"uuid": uuid}, withs=withs)

    async def list_by_user_and_agent(self, user_id: int, agent_instance_id: int, page: int, limit: int) -> List[ChatSession]:
        """获取用户在特定 Agent 实例下的会话列表，排除已归档的"""
        return await self.get_list(
            where={
                "user_id": user_id, 
                "agent_instance_id": agent_instance_id,
                "is_archived": False
            },
            order=[desc(ChatSession.updated_at)],
            page=page,
            limit=limit
        )

class ChatMessageDao(BaseDao[ChatMessage]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(ChatMessage, db_session)

    async def get_by_uuid(self, uuid: str, withs: list = None) -> Optional[ChatMessage]:
        return await self.get_one(where={"uuid": uuid}, withs=withs)

    async def get_active_count(self, session_id: int) -> int:
        """
        [Source of Truth] 获取当前会话的有效消息总数。
        严谨过滤掉 is_deleted=True 的消息。
        """
        stmt = (
            select(func.count())
            .select_from(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.is_deleted == False  # 核心：排除软删除
            )
        )
        result = await self.db_session.execute(stmt)
        return result.scalar() or 0

    async def get_active_turn_count(self, session_id: int) -> int:
        stmt = (
            select(func.count(func.distinct(ChatMessage.turn_id)))
            .select_from(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.is_deleted == False,
                ChatMessage.turn_id.is_not(None),
            )
        )
        result = await self.db_session.execute(stmt)
        return result.scalar() or 0

    async def get_existing_turn_ids(self, session_id: int, turn_ids: list[str]) -> set[str]:
        normalized_turn_ids = [turn_id for turn_id in turn_ids if isinstance(turn_id, str) and turn_id]
        if not normalized_turn_ids:
            return set()

        stmt = (
            select(ChatMessage.turn_id)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.is_deleted == False,
                ChatMessage.turn_id.in_(normalized_turn_ids),
            )
            .group_by(ChatMessage.turn_id)
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
            .select_from(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.is_deleted == False,
                ChatMessage.turn_id == turn_id,
            )
        )
        result = await self.db_session.execute(stmt)
        return result.scalar() or 0
        
    async def get_active_messages(self, session_id: int, page: int = 0, limit: int = 0) -> List[ChatMessage]:
        """
        获取活跃的历史消息 (用于构建 LLM Context)。
        [关键] 必须排除 is_deleted=True 的消息。
        """
        stmt = (
            select(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.is_deleted == False # 核心过滤
            )
            .order_by(desc(ChatMessage.id)) # 先倒序取最近的
            .limit(limit)
        )
        if page > 0 and limit > 0:
            stmt = self._paginate(stmt=stmt, page=page, limit=limit)
        result = await self.db_session.execute(stmt)
        messages = list(result.scalars().all())
        messages.reverse() # 转回正序 (时间顺序)
        return messages

    async def get_messages_by_turns(self, session_id: int, turns: int) -> List[ChatMessage]:
        """
        [Smart Retrieval] 获取最近 N 轮（基于 turn_id 分组）的消息。
        """
        # 1. 子查询：找出最近的 N 个 turn_id
        subquery = (
            select(ChatMessage.turn_id)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.is_deleted == False,
                ChatMessage.turn_id.is_not(None)
            )
            .group_by(ChatMessage.turn_id)
            .order_by(func.max(ChatMessage.id).desc())
            .limit(turns)
        ).scalar_subquery()
        
        stmt = (
            select(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.is_deleted == False,
                ChatMessage.turn_id.in_(subquery)
            )
            .order_by(ChatMessage.id.asc())
        )
        
        result = await self.db_session.execute(stmt)
        return list(result.scalars().all())
        
    async def get_history_for_frontend(self, session_id: int, cursor_id: int = 0, limit: int = 20) -> List[ChatMessage]:
        """获取前端展示用的历史记录，包含已软删除的(如果前端需要展示删除标记)？通常不需要展示已清除的。"""
        # 这里的逻辑取决于业务：清空上下文后，用户还要看到历史吗？
        # 定义中“清空”意味着“屏幕清空”。所以前端也不应该拉取到。
        where = [
            ChatMessage.session_id == session_id,
            ChatMessage.is_deleted == False
        ]
        if cursor_id > 0:
            where.append(ChatMessage.id < cursor_id)
            
        return await self.get_list(
            where=where,
            order=[desc(ChatMessage.id)],
            limit=limit
        )

    async def soft_delete_by_session(self, session_id: int):
        """[生产模式] 软删除会话下的所有消息"""
        stmt = (
            update(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .values(is_deleted=True)
        )
        await self.db_session.execute(stmt)

    async def physical_delete_by_session(self, session_id: int):
        """[调试模式] 物理删除会话下的所有消息"""
        stmt = (
            delete(ChatMessage)
            .where(ChatMessage.session_id == session_id)
        )
        await self.db_session.execute(stmt)
