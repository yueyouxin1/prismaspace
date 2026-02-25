# src/app/services/auditing/trace_service.py

from sqlalchemy.ext.asyncio import AsyncSession
from arq.connections import ArqRedis
from pydantic import BaseModel
from typing import Optional, Dict, Any
from decimal import Decimal

from app.core.context import AppContext
from app.models import Trace
from app.dao.auditing.trace_dao import TraceDao
from app.services.base_service import BaseService
from .types.trace import TraceCreateParams

class TraceService(BaseService):
    """
    [CRITICAL CHANGE - Service Layer] Responsible for the business logic of creating and managing
    auditable trace records. This service is context-aware and can perform authorization.
    """
    def __init__(self, context: AppContext):
        self.context = context
        self.db = context.db
        self.arq_pool: ArqRedis = context.arq_pool 
        self.dao = TraceDao(self.db)

    async def create_trace(self, params: TraceCreateParams):
        # [FUTURE-PROOFING] Permission check example:
        # await self.context.perm_evaluator.ensure_can(["trace:create"], target=target_workspace, actor=actor)

        new_trace = Trace(**params.model_dump())
        await self.dao.add(new_trace)
        return new_trace