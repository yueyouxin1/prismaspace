from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.trace_manager import TraceManager
from app.schemas.resource.tenantdb.tenantdb_schemas import (
    TenantDbExecutionParams,
    TenantDbExecutionRequest,
)
from app.services.resource.tenantdb_service import TenantDbService


pytestmark = pytest.mark.asyncio


class _FakeAsyncSession:
    def __init__(self):
        self.added = []

    def add_all(self, items):
        self.added.extend(items)

    async def flush(self):
        return None


async def test_tenantdb_service_nested_trace_uses_parent_source_and_target():
    service = object.__new__(TenantDbService)
    db = _FakeAsyncSession()
    actor = SimpleNamespace(id=1)
    workspace = SimpleNamespace(billing_owner=SimpleNamespace())
    table_meta = SimpleNamespace(name="users")
    instance = SimpleNamespace(
        id=404,
        schema_name="tenant_demo",
        tables=[table_meta],
        resource=SimpleNamespace(workspace=workspace),
    )

    service.db = db
    service.get_by_uuid = AsyncMock(return_value=instance)
    service._check_execute_perm = AsyncMock(return_value=None)
    service._get_sqlalchemy_table_object = lambda table_meta, schema_name: SimpleNamespace(name=f"{schema_name}.{table_meta.name}")
    service._query_rows = AsyncMock(return_value=([{"id": 1}], 1))

    async with TraceManager(
        db=db,
        operation_name="agent.run",
        user_id=actor.id,
        force_trace_id="trace-tenantdb-nested-1",
        target_instance_id=101,
    ):
        response = await service.execute(
            instance_uuid="tenantdb-1",
            execute_params=TenantDbExecutionRequest(
                inputs=TenantDbExecutionParams(action="query", table_name="users"),
            ),
            actor=actor,
            runtime_workspace=workspace,
        )

    assert response.data == [{"id": 1}]
    assert len(db.added) == 2
    root, child = db.added
    assert root.operation_name == "agent.run"
    assert child.operation_name == "tenantdb.execute"
    assert child.parent_span_uuid == root.span_uuid
    assert child.source_instance_id == 101
    assert child.target_instance_id == 404
