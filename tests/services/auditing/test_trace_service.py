from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.auditing.trace_service import TraceService


pytestmark = pytest.mark.asyncio


def _trace(
    *,
    span_uuid: str,
    trace_id: str = "trace-1",
    parent_span_uuid: str | None = None,
    operation_name: str,
    created_at: datetime,
    duration_ms: int,
    processed_at: datetime | None = None,
    status: str = "processed",
):
    return SimpleNamespace(
        span_uuid=span_uuid,
        trace_id=trace_id,
        parent_span_uuid=parent_span_uuid,
        operation_name=operation_name,
        user_id=1,
        source_instance_id=None,
        target_instance_id=None,
        status=status,
        duration_ms=duration_ms,
        error_message=None,
        context_type="run",
        context_id="run-1",
        created_at=created_at,
        processed_at=processed_at or (created_at + timedelta(milliseconds=duration_ms)),
        attributes={"ok": True},
    )


async def test_trace_service_builds_call_tree_and_flamegraph():
    service = TraceService(SimpleNamespace(db=None, arq_pool=None))
    baseline = datetime(2026, 3, 9, 12, 0, 0)
    traces = [
        _trace(span_uuid="root", operation_name="agent.run", created_at=baseline, duration_ms=100),
        _trace(
            span_uuid="child-a",
            parent_span_uuid="root",
            operation_name="tool.execute",
            created_at=baseline + timedelta(milliseconds=10),
            duration_ms=30,
        ),
        _trace(
            span_uuid="child-b",
            parent_span_uuid="root",
            operation_name="knowledge.batch_search",
            created_at=baseline + timedelta(milliseconds=50),
            duration_ms=20,
        ),
    ]
    service.dao = SimpleNamespace(list_by_trace_id=lambda trace_id: traces)

    tree = service._build_trace_tree(traces)
    assert tree.summary.trace_id == "trace-1"
    assert tree.summary.total_spans == 3
    assert tree.summary.operation_counts["tool.execute"] == 1
    assert tree.roots[0].span.span_uuid == "root"
    assert tree.roots[0].span.self_duration_ms == 50
    assert tree.roots[0].children[0].span.depth == 1

    async def _get_tree(trace_id: str):
        return tree

    service.get_trace_tree = _get_tree
    flamegraph = await TraceService.get_trace_flamegraph(service, "trace-1")
    assert flamegraph.trace_id == "trace-1"
    assert flamegraph.root is not None
    assert flamegraph.root.name == "agent.run"
    assert flamegraph.root.self_ms == 50


async def test_trace_service_raises_for_missing_trace():
    service = TraceService(SimpleNamespace(db=None, arq_pool=None))
    service.dao = SimpleNamespace(list_by_trace_id=lambda trace_id: [])

    with pytest.raises(Exception, match="Trace not found"):
        service._build_trace_tree([])
