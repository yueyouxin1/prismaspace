import pytest

from app.engine.model.llm import LLMMessage, LLMResult, LLMUsage
from app.models.resource import Resource


pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_agent_llm_engine(monkeypatch):
    class _MockLLMEngine:
        async def run(self, provider_config, run_config, messages, callbacks):
            await callbacks.on_start()
            await callbacks.on_chunk_generated("hello ")
            await callbacks.on_chunk_generated("world")
            result = LLMResult(
                message=LLMMessage(role="assistant", content="hello world"),
                usage=LLMUsage(prompt_tokens=5, completion_tokens=7, total_tokens=12),
            )
            await callbacks.on_success(result)
            await callbacks.on_usage(result.usage)
            return result

        async def clear_request_scoped_clients(self):
            return None

    monkeypatch.setattr(
        "app.services.common.llm_capability_provider.LLMEngineService",
        lambda *args, **kwargs: _MockLLMEngine(),
    )


@pytest.fixture
async def agent_resource(created_resource_factory) -> Resource:
    return await created_resource_factory("agent")


async def test_agent_execute_persists_run_detail_and_events(
    client,
    auth_headers_factory,
    registered_user_with_pro,
    agent_resource,
    mock_agent_llm_engine,
):
    headers = await auth_headers_factory(registered_user_with_pro)
    instance_uuid = agent_resource.workspace_instance.uuid

    execute_response = await client.post(
        f"/api/v1/agent/{instance_uuid}/execute",
        json={
            "threadId": "thread-agent-run",
            "runId": "run-agent-api-1",
            "state": {},
            "messages": [{"id": "u1", "role": "user", "content": "hello"}],
            "tools": [],
            "context": [],
            "forwardedProps": {"platform": {"sessionMode": "stateless"}},
        },
        headers=headers,
    )
    assert execute_response.status_code == 200, execute_response.text
    payload = execute_response.json()["data"]
    run_id = payload["runId"]

    run_response = await client.get(
        f"/api/v1/agent/runs/{run_id}",
        headers=headers,
    )
    assert run_response.status_code == 200, run_response.text
    run_data = run_response.json()["data"]
    assert run_data["run_id"] == run_id
    assert run_data["status"] == "succeeded"
    assert len(run_data["events"]) >= 2
    assert run_data["latest_checkpoint"] is None

    events_response = await client.get(
        f"/api/v1/agent/runs/{run_id}/events",
        headers=headers,
    )
    assert events_response.status_code == 200, events_response.text
    event_types = [item["event_type"] for item in events_response.json()["data"]]
    assert "RUN_STARTED" in event_types
    assert "RUN_FINISHED" in event_types
