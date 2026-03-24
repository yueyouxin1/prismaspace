from types import SimpleNamespace

import pytest

from app.services.resource.workflow.run_persistence import WorkflowRunPersistenceService


pytestmark = pytest.mark.asyncio


async def test_append_events_for_ids_batches_sequence_and_flush_once():
    added = []
    flush_calls = []

    class FakeDao:
        async def get_last_event(self, *, resource_execution_id: int):
            return SimpleNamespace(sequence_no=3)

    class FakeDb:
        def add(self, obj):
            added.append((obj.event_type, obj.sequence_no, obj.payload))

        async def flush(self):
            flush_calls.append("flush")

    service = WorkflowRunPersistenceService.__new__(WorkflowRunPersistenceService)
    service.db = FakeDb()
    service.event_dao = FakeDao()

    await service.append_events_for_ids(
        execution_id=11,
        workflow_instance_id=22,
        events=[
            {"event_type": "stream.delta", "payload": {"chunk": "A"}},
            {"event_type": "stream.delta", "payload": {"chunk": "B"}},
            {"event_type": "run.finished", "payload": {"output": {"ok": True}}},
        ],
    )

    assert added == [
        ("stream.delta", 4, {"chunk": "A"}),
        ("stream.delta", 5, {"chunk": "B"}),
        ("run.finished", 6, {"output": {"ok": True}}),
    ]
    assert flush_calls == ["flush"]
