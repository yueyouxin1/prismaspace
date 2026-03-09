from types import SimpleNamespace

import pytest

from app.api.v1.auditing import trace_api


@pytest.mark.asyncio
async def test_trace_routes_delegate_to_service(monkeypatch):
    class _FakeTraceService:
        def __init__(self, context):
            self.context = context

        async def get_trace_summary(self, trace_id):
            return {"trace_id": trace_id, "root_span_uuid": "root", "total_spans": 1, "total_duration_ms": 10, "status_counts": {}, "operation_counts": {}, "spans": []}

        async def get_trace_tree(self, trace_id):
            return {
                "summary": {"trace_id": trace_id, "root_span_uuid": "root", "total_spans": 1, "total_duration_ms": 10, "status_counts": {}, "operation_counts": {}, "spans": []},
                "roots": [],
            }

        async def get_trace_flamegraph(self, trace_id):
            return {"trace_id": trace_id, "total_duration_ms": 10, "root": None}

    monkeypatch.setattr(trace_api, "TraceService", _FakeTraceService)
    context = SimpleNamespace(actor=SimpleNamespace())

    summary = await trace_api.get_trace_summary("trace-1", context)
    assert summary.data["trace_id"] == "trace-1"

    tree = await trace_api.get_trace_tree("trace-1", context)
    assert tree.data["summary"]["trace_id"] == "trace-1"

    flamegraph = await trace_api.get_trace_flamegraph("trace-1", context)
    assert flamegraph.data["trace_id"] == "trace-1"
