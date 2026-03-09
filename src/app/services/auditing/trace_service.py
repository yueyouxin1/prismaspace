# src/app/services/auditing/trace_service.py

from collections import Counter
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from arq.connections import ArqRedis
from pydantic import BaseModel
from typing import Optional, Dict, Any, List, Tuple
from decimal import Decimal

from app.core.context import AppContext
from app.models import Trace
from app.dao.auditing.trace_dao import TraceDao
from app.services.base_service import BaseService
from app.services.exceptions import NotFoundError
from app.schemas.auditing.trace_schemas import (
    TraceFlamegraphNodeRead,
    TraceFlamegraphRead,
    TraceSpanRead,
    TraceSummaryRead,
    TraceTreeNodeRead,
    TraceTreeRead,
)
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

    @staticmethod
    def _duration_ms(value: Optional[int]) -> int:
        if isinstance(value, int) and value >= 0:
            return value
        return 0

    @staticmethod
    def _status_value(value: Any) -> str:
        return value.value if hasattr(value, "value") else str(value)

    @staticmethod
    def _ms_between(start: Optional[datetime], current: Optional[datetime]) -> int:
        if start is None or current is None:
            return 0
        return max(int((current - start).total_seconds() * 1000), 0)

    def _build_trace_tree(self, traces: List[Trace]) -> TraceTreeRead:
        if not traces:
            raise NotFoundError("Trace not found.")

        baseline = min((trace.created_at for trace in traces if trace.created_at is not None), default=None)
        spans: Dict[str, TraceSpanRead] = {}
        children_map: Dict[Optional[str], List[str]] = {}

        for trace in traces:
            status_value = self._status_value(trace.status)
            duration_ms = self._duration_ms(trace.duration_ms)
            start_offset_ms = self._ms_between(baseline, trace.created_at)
            span = TraceSpanRead(
                span_uuid=trace.span_uuid,
                parent_span_uuid=trace.parent_span_uuid,
                operation_name=trace.operation_name,
                user_id=trace.user_id,
                source_instance_id=trace.source_instance_id,
                target_instance_id=trace.target_instance_id,
                status=status_value,
                duration_ms=duration_ms,
                self_duration_ms=duration_ms,
                start_offset_ms=start_offset_ms,
                end_offset_ms=start_offset_ms + duration_ms,
                error_message=trace.error_message,
                context_type=trace.context_type,
                context_id=trace.context_id,
                created_at=trace.created_at,
                processed_at=trace.processed_at,
                attributes=trace.attributes or {},
            )
            spans[trace.span_uuid] = span
            children_map.setdefault(trace.parent_span_uuid, []).append(trace.span_uuid)

        roots = [
            span_uuid
            for span_uuid, span in spans.items()
            if not span.parent_span_uuid or span.parent_span_uuid not in spans
        ]
        roots.sort(key=lambda span_uuid: (spans[span_uuid].start_offset_ms, spans[span_uuid].span_uuid))

        def _visit(span_uuid: str, depth: int) -> TraceTreeNodeRead:
            span = spans[span_uuid]
            child_ids = children_map.get(span_uuid, [])
            children = [_visit(child_id, depth + 1) for child_id in child_ids]
            child_total = sum(child.span.duration_ms for child in children)
            span.depth = depth
            span.self_duration_ms = max(span.duration_ms - child_total, 0)
            return TraceTreeNodeRead(span=span, children=children)

        root_nodes = [_visit(span_uuid, 0) for span_uuid in roots]
        status_counts = Counter(span.status for span in spans.values())
        operation_counts = Counter(span.operation_name for span in spans.values())
        root_span = spans[roots[0]] if roots else next(iter(spans.values()))
        total_duration_ms = max((span.end_offset_ms for span in spans.values()), default=0)
        finished_at = max((trace.processed_at for trace in traces if trace.processed_at is not None), default=None)

        summary = TraceSummaryRead(
            trace_id=traces[0].trace_id,
            root_span_uuid=root_span.span_uuid if root_span else None,
            total_spans=len(spans),
            total_duration_ms=total_duration_ms,
            started_at=baseline,
            finished_at=finished_at,
            status_counts=dict(status_counts),
            operation_counts=dict(operation_counts),
            context_type=root_span.context_type if root_span else None,
            context_id=root_span.context_id if root_span else None,
            spans=sorted(spans.values(), key=lambda item: (item.start_offset_ms, item.span_uuid)),
        )
        return TraceTreeRead(summary=summary, roots=root_nodes)

    @staticmethod
    def _to_flamegraph_node(node: TraceTreeNodeRead) -> TraceFlamegraphNodeRead:
        return TraceFlamegraphNodeRead(
            name=node.span.operation_name,
            span_uuid=node.span.span_uuid,
            value_ms=node.span.duration_ms,
            self_ms=node.span.self_duration_ms,
            start_offset_ms=node.span.start_offset_ms,
            children=[TraceService._to_flamegraph_node(child) for child in node.children],
        )

    async def get_trace_tree(self, trace_id: str) -> TraceTreeRead:
        traces = await self.dao.list_by_trace_id(trace_id)
        return self._build_trace_tree(traces)

    async def get_trace_summary(self, trace_id: str) -> TraceSummaryRead:
        return (await self.get_trace_tree(trace_id)).summary

    async def get_trace_flamegraph(self, trace_id: str) -> TraceFlamegraphRead:
        tree = await self.get_trace_tree(trace_id)
        root = tree.roots[0] if tree.roots else None
        return TraceFlamegraphRead(
            trace_id=trace_id,
            total_duration_ms=tree.summary.total_duration_ms,
            root=self._to_flamegraph_node(root) if root else None,
        )
