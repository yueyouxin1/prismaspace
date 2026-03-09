import json
from types import SimpleNamespace

import pytest

from app.api.v1.agent import agent_api


async def _collect_sse_response_chunks(streaming_response):
    chunks = []
    async for chunk in streaming_response.body_iterator:
        chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
    return "".join(chunks)


@pytest.mark.asyncio
async def test_agent_run_query_routes_delegate_to_service(monkeypatch):
    class _FakeAgentService:
        def __init__(self, context):
            self.context = context

        async def list_runs(self, instance_uuid, limit=20):
            return [
                {
                    "run_id": "run-1",
                    "thread_id": "thread-1",
                    "parent_run_id": None,
                    "status": "succeeded",
                    "trace_id": "trace-1",
                    "error_code": None,
                    "error_message": None,
                    "started_at": None,
                    "finished_at": None,
                }
            ]

        async def get_run(self, run_id):
            return {
                "run_id": run_id,
                "thread_id": "thread-1",
                "parent_run_id": None,
                "status": "succeeded",
                "trace_id": "trace-1",
                "error_code": None,
                "error_message": None,
                "started_at": None,
                "finished_at": None,
                "agent_instance_uuid": "agent-1",
                "agent_name": "Agent 1",
                "events": [],
                "tool_executions": [],
            }

        async def list_run_events(self, run_id, limit=1000):
            return [
                {
                    "sequence_no": 1,
                    "event_type": "RUN_STARTED",
                    "payload": {"type": "RUN_STARTED", "runId": run_id},
                    "created_at": None,
                }
            ]

        async def cancel_run(self, run_id):
            return {"run_id": run_id, "accepted": True, "local_cancelled": False}

        async def get_active_run(self, instance_uuid, actor, thread_id):
            return {
                "run_id": "run-active",
                "thread_id": thread_id,
                "parent_run_id": None,
                "status": "running",
                "trace_id": "trace-1",
                "error_code": None,
                "error_message": None,
                "started_at": None,
                "finished_at": None,
            }

    monkeypatch.setattr(agent_api, "AgentService", _FakeAgentService)
    context = SimpleNamespace(actor=SimpleNamespace())

    runs_response = await agent_api.list_agent_runs("agent-1", 20, context)
    assert runs_response.data[0]["run_id"] == "run-1"

    run_response = await agent_api.get_agent_run("run-1", context)
    assert run_response.data["run_id"] == "run-1"

    events_response = await agent_api.list_agent_run_events("run-1", 1000, context)
    assert events_response.data[0]["event_type"] == "RUN_STARTED"

    active_response = await agent_api.get_active_agent_run("agent-1", "thread-1", context)
    assert active_response.data["run_id"] == "run-active"

    cancel_response = await agent_api.cancel_agent_run("run-1", context)
    assert cancel_response.data["accepted"] is True


@pytest.mark.asyncio
async def test_agent_run_replay_route_streams_persisted_payloads(monkeypatch):
    class _FakeAgentService:
        def __init__(self, context):
            self.context = context

        async def list_run_events(self, run_id, limit=1000):
            return [
                SimpleNamespace(event_type="RUN_STARTED", payload={"type": "RUN_STARTED", "runId": run_id}),
                SimpleNamespace(event_type="RUN_FINISHED", payload={"type": "RUN_FINISHED", "runId": run_id}),
            ]

    monkeypatch.setattr(agent_api, "AgentService", _FakeAgentService)
    context = SimpleNamespace(actor=SimpleNamespace())
    response = await agent_api.replay_agent_run_events("run-1", 1000, context)
    body = await _collect_sse_response_chunks(response)

    assert '"type": "RUN_STARTED"' in body
    assert '"type": "RUN_FINISHED"' in body


@pytest.mark.asyncio
async def test_agent_run_live_route_streams_envelopes(monkeypatch):
    class _FakeAgentService:
        def __init__(self, context):
            self.context = context

        async def stream_live_run_events(self, run_id, after_seq=0):
            for event_type in ("RUN_STARTED", "TEXT_MESSAGE_CONTENT", "RUN_FINISHED"):
                yield {"seq": after_seq + 1, "payload": {"type": event_type, "runId": run_id}}

    monkeypatch.setattr(agent_api, "AgentService", _FakeAgentService)
    context = SimpleNamespace(actor=SimpleNamespace())
    response = await agent_api.stream_live_agent_run_events("run-live", 0, context)
    body = await _collect_sse_response_chunks(response)

    assert '"type": "RUN_STARTED"' in body
    assert '"type": "RUN_FINISHED"' in body
