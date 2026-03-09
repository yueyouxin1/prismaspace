from datetime import timedelta

import pytest

from app.core.trace_manager import TraceManager


pytestmark = pytest.mark.asyncio


class _FakeAsyncSession:
    def __init__(self):
        self.added = []

    def add_all(self, items):
        self.added.extend(items)

    async def flush(self):
        return None


async def test_trace_manager_nested_resource_topology_builds_parent_and_source_target_chain():
    db = _FakeAsyncSession()

    async with TraceManager(
        db=db,
        operation_name="agent.run",
        user_id=1,
        force_trace_id="trace-topology-1",
        target_instance_id=101,
    ):
        async with TraceManager(
            db=db,
            operation_name="tool.execute",
            target_instance_id=202,
        ):
            async with TraceManager(
                db=db,
                operation_name="workflow.run",
                target_instance_id=303,
            ):
                pass

    assert len(db.added) == 3
    root, child, grandchild = db.added

    assert root.trace_id == child.trace_id == grandchild.trace_id == "trace-topology-1"
    assert root.parent_span_uuid is None
    assert child.parent_span_uuid == root.span_uuid
    assert grandchild.parent_span_uuid == child.span_uuid

    assert root.source_instance_id is None
    assert root.target_instance_id == 101
    assert child.source_instance_id == 101
    assert child.target_instance_id == 202
    assert grandchild.source_instance_id == 202
    assert grandchild.target_instance_id == 303


async def test_trace_manager_on_before_flush_hook_runs_for_root_trace():
    db = _FakeAsyncSession()
    observed = []

    async with TraceManager(
        db=db,
        operation_name="agent.run",
        user_id=1,
        force_trace_id="trace-hook-1",
        target_instance_id=101,
    ):
        TraceManager.on_before_flush(lambda: _record_hook(observed))

    assert observed == ["hook-ran"]
    assert len(db.added) == 1


async def _record_hook(observed: list[str]) -> None:
    observed.append("hook-ran")
