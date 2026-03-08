from types import SimpleNamespace

import pytest

from app.api.v1.agent.ws_handler import AgentSessionHandler
from app.schemas.protocol import RunAgentInputExt


def _build_run_input(*, forwarded_props):
    return RunAgentInputExt.model_validate(
        {
            "threadId": "thread-x",
            "runId": "run-x",
            "state": {},
            "messages": [{"id": "u1", "role": "user", "content": "hello"}],
            "tools": [],
            "context": [],
            "forwardedProps": forwarded_props,
        }
    )


def test_extract_agent_uuid_reads_platform_websocket_contract():
    run_input = _build_run_input(forwarded_props={"platform": {"agentUuid": "agent-1"}})

    assert AgentSessionHandler._extract_agent_uuid(run_input) == "agent-1"


def test_extract_agent_uuid_returns_none_without_platform_payload():
    run_input = _build_run_input(forwarded_props={"trace": "123"})

    assert AgentSessionHandler._extract_agent_uuid(run_input) is None


@pytest.mark.asyncio
async def test_missing_agent_uuid_error_mentions_websocket_only_platform_field():
    handler = AgentSessionHandler(SimpleNamespace(), SimpleNamespace(user=SimpleNamespace(uuid="user-1")))
    events = []

    async def _capture(payload):
        events.append(payload.model_dump(mode="json", by_alias=True, exclude_none=True))

    handler._send_event = _capture

    await handler._send_run_error(
        run_id="run-x",
        thread_id="thread-x",
        code="AG_UI_MISSING_AGENT_UUID",
        message="Missing websocket-only agent uuid in forwardedProps.platform.agentUuid",
    )

    assert events[0]["code"] == "AG_UI_MISSING_AGENT_UUID"
    assert events[0]["message"] == "Missing websocket-only agent uuid in forwardedProps.platform.agentUuid"
