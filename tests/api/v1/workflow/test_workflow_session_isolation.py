from types import SimpleNamespace

import pytest

from app.core.context import AppContext
from app.services.resource.workflow.runtime_persistence import (
    WorkflowDurableRuntimeObserver,
    WorkflowRuntimePersistenceService,
)


class _FakeTransaction:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None


class _FakeSession:
    def __init__(self, label: str):
        self.label = label

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None

    def begin(self):
        return _FakeTransaction()


@pytest.mark.asyncio
async def test_runtime_observer_uses_isolated_session_for_node_persistence(monkeypatch):
    original_db = SimpleNamespace(label="original-db")
    isolated_db = _FakeSession("isolated-db")
    seen_dbs: list[str] = []

    async def _fake_upsert(self, **kwargs):
        seen_dbs.append(self.db.label)
        return None

    monkeypatch.setattr(WorkflowRuntimePersistenceService, "upsert_node_execution", _fake_upsert)

    context = AppContext.model_construct(
        db=original_db,
        db_session_factory=lambda: isolated_db,
        auth=None,
        redis_service=SimpleNamespace(),
        vector_manager=SimpleNamespace(),
        arq_pool=SimpleNamespace(),
    )
    observer = WorkflowDurableRuntimeObserver(
        context=context,
        execution=SimpleNamespace(id=11, run_id="run-1", thread_id="thread-1"),
        workflow_instance=SimpleNamespace(id=22),
        runtime_plan=SimpleNamespace(),
    )

    await observer.on_node_state(
        node=SimpleNamespace(id="node-1", name="Node 1", registry_id="LLM"),
        state=SimpleNamespace(),
        reason="node_streamtask",
    )

    assert seen_dbs == ["isolated-db"]
