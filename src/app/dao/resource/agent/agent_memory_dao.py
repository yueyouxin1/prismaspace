from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, delete, desc
from app.dao.base_dao import BaseDao
from app.models.resource.agent.agent_memory import AgentMemoryVar, AgentMemoryVarValue
from app.models.resource.agent.agent_memory import AgentContextSummary, SummaryScope

class AgentMemoryVarDao(BaseDao[AgentMemoryVar]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(AgentMemoryVar, db_session)

    async def get_by_agent_and_key(self, agent_id: int, key: str) -> AgentMemoryVar | None:
        return await self.get_one(where={"agent_id": agent_id, "key": key})

class AgentMemoryVarValueDao(BaseDao[AgentMemoryVarValue]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(AgentMemoryVarValue, db_session)

    async def get_value(self, memory_id: int, user_id: int = None, session_uuid: str = None):
        """根据作用域获取值"""
        conditions = [AgentMemoryVarValue.memory_id == memory_id]
        
        # 构造精确的作用域查询
        if user_id:
            conditions.append(AgentMemoryVarValue.user_id == user_id)
        if session_uuid:
            conditions.append(AgentMemoryVarValue.session_uuid == session_uuid)
            
        stmt = select(AgentMemoryVarValue).where(and_(*conditions))
        result = await self.db_session.execute(stmt)
        return result.scalars().first()

class AgentContextSummaryDao(BaseDao[AgentContextSummary]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(AgentContextSummary, db_session)

    async def get_active_summaries(
        self, 
        agent_instance_id: int, 
        user_id: int, 
        session_uuid: Optional[str] = None,
        exclude_trace_ids: Optional[List[str]] = None,
        page: int = 0,
        limit: int = 0
    ):
        """
        获取当前上下文有效的摘要列表。
        包含：
        1. 该用户的 USER 级摘要
        2. 该会话的 SESSION 级摘要 (如果提供了 session_uuid)
        且未归档。
        """
        filters = [
            AgentContextSummary.agent_instance_id == agent_instance_id,
            AgentContextSummary.user_id == user_id,
            AgentContextSummary.is_archived == False
        ]

        if exclude_trace_ids:
            filters.append(AgentContextSummary.trace_id.not_in(exclude_trace_ids))
        
        # 构建 OR 条件：Scope=USER OR (Scope=SESSION AND session_uuid=...)
        scope_condition = (AgentContextSummary.scope == SummaryScope.USER)
        if session_uuid:
            scope_condition = or_(
                scope_condition,
                and_(
                    AgentContextSummary.scope == SummaryScope.SESSION,
                    AgentContextSummary.session_uuid == session_uuid
                )
            )
        
        stmt = (
            select(AgentContextSummary)
            .where(and_(*filters, scope_condition))
            .order_by(
                desc(AgentContextSummary.ref_created_at), # 第一优先级：对话时间
                desc(AgentContextSummary.created_at)      # 第二优先级：生成时间
            )
        )
        if page > 0 and limit > 0:
            stmt = self._paginate(stmt=stmt, page=page, limit=limit)
        result = await self.db_session.execute(stmt)
        return list(result.scalars().all())

    async def soft_delete_by_trace_id(self, trace_id: str):
        """[软删除] 归档特定 Trace 的摘要"""
        stmt = (
            update(AgentContextSummary)
            .where(AgentContextSummary.trace_id == trace_id)
            .values(is_archived=True)
        )
        await self.db_session.execute(stmt)

    async def physical_delete_by_trace_id(self, trace_id: str):
        """[硬删除] 物理清除特定 Trace 的摘要"""
        stmt = delete(AgentContextSummary).where(AgentContextSummary.trace_id == trace_id)
        await self.db_session.execute(stmt)
    
    async def soft_delete_by_session_uuid(self, session_uuid: str):
        """[级联] 当会话归档时，归档其下所有 Session Scope 的摘要"""
        stmt = (
            update(AgentContextSummary)
            .where(
                AgentContextSummary.session_uuid == session_uuid,
                AgentContextSummary.scope == SummaryScope.SESSION
            )
            .values(is_archived=True)
        )
        await self.db_session.execute(stmt)