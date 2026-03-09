from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.trace_manager import TraceManager
from app.models.resource import VersionStatus
from app.schemas.resource.tool_schemas import ToolExecutionRequest
from app.services.resource import tool_service as tool_service_module
from app.services.resource.tool_service import ToolService


pytestmark = pytest.mark.asyncio


class _FakeAsyncSession:
    def __init__(self):
        self.added = []

    def add_all(self, items):
        self.added.extend(items)

    async def flush(self):
        return None


class _FakeBillingContext:
    def __init__(self, _context, _billing_entity):
        self._context = _context
        self._billing_entity = _billing_entity

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None


async def test_tool_service_nested_trace_uses_parent_source_and_target(monkeypatch):
    monkeypatch.setattr(tool_service_module, "BillingContext", _FakeBillingContext)

    service = object.__new__(ToolService)
    db = _FakeAsyncSession()
    actor = SimpleNamespace(id=1)
    workspace = SimpleNamespace(billing_owner=SimpleNamespace())
    instance = SimpleNamespace(
        id=202,
        uuid="tool-1",
        name="Weather Tool",
        method="GET",
        url="https://example.com/weather",
        linked_feature=None,
        status=VersionStatus.WORKSPACE,
        inputs_schema=[],
        outputs_schema=[],
        resource=SimpleNamespace(workspace=workspace),
    )

    service.db = db
    service.context = SimpleNamespace()
    service.engine = SimpleNamespace(run=AsyncMock(return_value={"ok": True}))
    service.get_by_uuid = AsyncMock(return_value=instance)
    service._check_execute_perm = AsyncMock(return_value=None)

    async with TraceManager(
        db=db,
        operation_name="agent.run",
        user_id=actor.id,
        force_trace_id="trace-tool-nested-1",
        target_instance_id=101,
    ):
        response = await service.execute(
            instance_uuid="tool-1",
            execute_params=ToolExecutionRequest(inputs={"city": "beijing"}),
            actor=actor,
            runtime_workspace=workspace,
        )

    assert response.data == {"ok": True}
    assert len(db.added) == 2
    root, child = db.added
    assert root.operation_name == "agent.run"
    assert child.operation_name == "tool.execute"
    assert child.parent_span_uuid == root.span_uuid
    assert child.source_instance_id == 101
    assert child.target_instance_id == 202
