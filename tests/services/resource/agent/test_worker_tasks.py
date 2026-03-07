from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.schemas.resource.agent.agent_schemas import AgentConfig
from app.worker import CRON_JOBS, TASK_FUNCTIONS, WorkerSettings
from app.worker.tasks import agent as agent_tasks


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _stmt):
        return _FakeExecuteResult(self._rows)

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
async def test_index_turn_task_reraises_background_failures(monkeypatch):
    fake_session = _FakeSession(rows=[SimpleNamespace(id=1)])
    monkeypatch.setattr(
        agent_tasks,
        "rebuild_context_for_worker",
        AsyncMock(return_value=SimpleNamespace()),
    )
    monkeypatch.setattr(
        agent_tasks,
        "LongTermContextService",
        lambda _context: SimpleNamespace(
            index_turn_background=AsyncMock(side_effect=RuntimeError("index boom"))
        ),
    )

    with pytest.raises(RuntimeError, match="index boom"):
        await agent_tasks.index_turn_task(
            ctx={"db_session_factory": lambda: _FakeSessionContext(fake_session)},
            agent_instance_id=7,
            session_uuid="session-1",
            run_id="run-1",
            turn_id="turn-1",
            runtime_workspace_id=9,
            user_uuid="user-1",
            trace_id="trace-1",
        )


@pytest.mark.asyncio
async def test_summarize_turn_task_reraises_background_failures(monkeypatch):
    fake_session = _FakeSession(rows=[SimpleNamespace(id=1)])
    monkeypatch.setattr(
        agent_tasks,
        "rebuild_context_for_worker",
        AsyncMock(return_value=SimpleNamespace()),
    )
    monkeypatch.setattr(
        agent_tasks,
        "AgentDao",
        lambda _session: SimpleNamespace(
            get_by_pk=AsyncMock(
                return_value=SimpleNamespace(agent_config=AgentConfig().model_dump(mode="json"))
            )
        ),
    )
    monkeypatch.setattr(
        agent_tasks,
        "ContextSummaryService",
        lambda _context: SimpleNamespace(
            summarize_turn_background=AsyncMock(side_effect=RuntimeError("summary boom"))
        ),
    )

    with pytest.raises(RuntimeError, match="summary boom"):
        await agent_tasks.summarize_turn_task(
            ctx={"db_session_factory": lambda: _FakeSessionContext(fake_session)},
            agent_instance_id=7,
            session_uuid="session-1",
            run_id="run-1",
            turn_id="turn-1",
            runtime_workspace_id=9,
            user_uuid="user-1",
            trace_id="trace-1",
        )


def test_worker_registry_exposes_agent_tasks_and_cron_jobs():
    task_names = {getattr(task, "__name__", "") for task in TASK_FUNCTIONS}

    assert "index_turn_task" in task_names
    assert "summarize_turn_task" in task_names
    assert WorkerSettings.cron_jobs is CRON_JOBS
    assert CRON_JOBS
