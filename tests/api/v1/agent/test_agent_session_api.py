from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.agent import session_api
from app.schemas.resource.agent.session_schemas import (
    AgentMessageRead,
    AgentSessionClearContextRequest,
    AgentSessionCreate,
    AgentSessionUpdate,
)
from app.services.exceptions import NotFoundError, PermissionDeniedError


@pytest.mark.asyncio
async def test_agent_session_router_exposes_expected_paths():
    paths = {route.path for route in session_api.router.routes}
    assert "/sessions" in paths
    assert "/sessions/{session_uuid}" in paths
    assert "/sessions/{session_uuid}/messages" in paths
    assert "/sessions/{session_uuid}/clear" in paths


@pytest.mark.asyncio
async def test_create_session_route_uses_agent_session_service(monkeypatch):
    created = SimpleNamespace(uuid="session-1")

    class _FakeService:
        def __init__(self, context):
            self.context = context

        async def create_session(self, data, actor):
            assert data.agent_instance_uuid == "agent-1"
            assert actor.user_id == "user-1"
            return created

    monkeypatch.setattr(session_api, "AgentSessionService", _FakeService)

    context = SimpleNamespace(actor=SimpleNamespace(user_id="user-1"))
    response = await session_api.create_session(
        AgentSessionCreate(agent_instance_uuid="agent-1"),
        context,
    )

    assert response.data is created


@pytest.mark.asyncio
async def test_get_session_history_route_returns_service_payload(monkeypatch):
    message = AgentMessageRead.model_validate(
        {
            "uuid": "msg-1",
            "role": "assistant",
            "content": "hello",
            "created_at": "2026-03-08T00:00:00Z",
        }
    )

    class _FakeService:
        def __init__(self, context):
            self.context = context

        async def get_session_history(self, session_uuid, cursor, limit, actor):
            assert session_uuid == "session-1"
            assert cursor == 0
            assert limit == 20
            assert actor.user_id == "user-1"
            return [message]

    monkeypatch.setattr(session_api, "AgentSessionService", _FakeService)

    context = SimpleNamespace(actor=SimpleNamespace(user_id="user-1"))
    response = await session_api.get_session_history("session-1", 0, 20, context)

    assert response.data == [message]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (NotFoundError("missing"), 404),
        (PermissionDeniedError("denied"), 403),
    ],
)
async def test_rename_session_route_maps_service_errors(monkeypatch, error, expected_status):
    class _FakeService:
        def __init__(self, context):
            self.context = context

        async def rename_session(self, session_uuid, title, actor):
            raise error

    monkeypatch.setattr(session_api, "AgentSessionService", _FakeService)

    context = SimpleNamespace(actor=SimpleNamespace(user_id="user-1"))
    with pytest.raises(HTTPException) as exc_info:
        await session_api.rename_session(
            "session-1",
            AgentSessionUpdate(title="Renamed"),
            context,
        )

    assert exc_info.value.status_code == expected_status


@pytest.mark.asyncio
async def test_clear_session_context_route_passes_mode(monkeypatch):
    observed = {}

    class _FakeService:
        def __init__(self, context):
            self.context = context

        async def clear_context(self, session_uuid, mode, actor):
            observed["session_uuid"] = session_uuid
            observed["mode"] = mode
            observed["actor_id"] = actor.user_id

    monkeypatch.setattr(session_api, "AgentSessionService", _FakeService)

    context = SimpleNamespace(actor=SimpleNamespace(user_id="user-1"))
    response = await session_api.clear_session_context(
        "session-1",
        AgentSessionClearContextRequest(mode="debug"),
        context,
    )

    assert observed == {
        "session_uuid": "session-1",
        "mode": "debug",
        "actor_id": "user-1",
    }
    assert response.msg == "Context cleared."
