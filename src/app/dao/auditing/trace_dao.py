# src/app/dao/auditing/trace_dao.py

from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from sqlalchemy import asc
from app.dao.base_dao import BaseDao
from app.models.auditing import Trace

class TraceDao(BaseDao[Trace]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(Trace, db_session)

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[Trace]:
        """Finds a trace by its UUID (if we decide to add one). For now, it's by ID."""
        # Note: Trace currently uses primary key 'id' for enqueueing.
        # If trace_id (UUID) is exposed for external reference, this method would be useful.
        raise NotImplementedError("Trace is primarily retrieved by its ID for internal processing.")

    async def list_by_trace_id(self, trace_id: str) -> list[Trace]:
        return await self.get_list(
            where={"trace_id": trace_id},
            order=[asc(Trace.created_at), asc(Trace.id)],
        )
