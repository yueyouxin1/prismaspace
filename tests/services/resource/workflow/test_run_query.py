from types import SimpleNamespace

import pytest

from app.services.resource.workflow.run_query import WorkflowRunQueryService


pytestmark = pytest.mark.asyncio


async def test_stream_live_run_events_uses_live_buffer_only():
    seen = []

    class _Service:
        execution_ledger_service = SimpleNamespace(
            get_by_run_id=lambda run_id: None,
        )

    async def _get_by_run_id(run_id):
        return SimpleNamespace(id=11, resource_instance_id=22)

    async def _get_by_pk(resource_instance_id):
        return SimpleNamespace(uuid="wf-1")

    async def _get_by_uuid(uuid):
        return SimpleNamespace(uuid=uuid)

    async def _check_execute_perm(instance):
        seen.append(("perm", instance.uuid))

    async def _stream_events(run_id, after_seq=0):
        seen.append(("stream", run_id, after_seq))
        yield {"seq": 2, "payload": {"event": "node.started", "data": {}}}
        yield {"seq": 3, "payload": {"event": "run.finished", "data": {"output": {"ok": True}}}}

    service = SimpleNamespace(
        execution_ledger_service=SimpleNamespace(get_by_run_id=_get_by_run_id),
        dao=SimpleNamespace(get_by_pk=_get_by_pk),
        get_by_uuid=_get_by_uuid,
        _check_execute_perm=_check_execute_perm,
        live_event_service=SimpleNamespace(stream_events=_stream_events),
        run_persistence_service=SimpleNamespace(
            list_events_after_sequence=lambda **kwargs: (_ for _ in ()).throw(AssertionError("DB fallback should not be used"))
        ),
    )

    query_service = WorkflowRunQueryService(service)

    events = []
    async for envelope in query_service.stream_live_run_events("run-1", after_seq=1):
        events.append(envelope)

    assert seen == [("perm", "wf-1"), ("stream", "run-1", 1)]
    assert [item["seq"] for item in events] == [2, 3]
