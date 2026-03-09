from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.worker.tasks import workflow as workflow_tasks


class _FakeSession:
    @asynccontextmanager
    async def begin(self):
        yield self


class _FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None


@pytest.mark.asyncio
async def test_execute_workflow_run_task_rethrows_failures(monkeypatch):
    fake_session = _FakeSession()
    fake_service = SimpleNamespace(
        execute_precreated_run=AsyncMock(side_effect=RuntimeError("workflow boom"))
    )

    monkeypatch.setattr(
        workflow_tasks,
        "rebuild_context_for_worker",
        AsyncMock(return_value=SimpleNamespace(actor=SimpleNamespace(uuid="user-1"))),
    )

    class _FakeWorkflowService:
        def __init__(self, _context):
            self.execute_precreated_run = fake_service.execute_precreated_run

    monkeypatch.setitem(__import__("sys").modules, "app.services.resource.workflow.workflow_service", SimpleNamespace(WorkflowService=_FakeWorkflowService))

    with pytest.raises(RuntimeError, match="workflow boom"):
        await workflow_tasks.execute_workflow_run_task(
            ctx={"db_session_factory": lambda: _FakeSessionContext(fake_session)},
            run_id="run-1",
            instance_uuid="instance-1",
            actor_uuid="user-1",
            execute_params={"inputs": {}},
        )
