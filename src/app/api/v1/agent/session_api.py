# src/app/api/v1/agent/session_api.py

from fastapi import APIRouter, Query, status, HTTPException
from typing import List
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep
from app.schemas.common import JsonResponse, MsgResponse
from app.schemas.resource.agent.session_schemas import (
    AgentSessionCreate,
    AgentSessionRead,
    AgentMessageRead,
    AgentSessionUpdate,
    AgentSessionClearContextRequest,
)
from app.services.resource.agent.session_service import AgentSessionService
from app.services.exceptions import NotFoundError, PermissionDeniedError

router = APIRouter()

@router.post("/sessions", response_model=JsonResponse[AgentSessionRead], status_code=status.HTTP_201_CREATED, summary="Create a new agent session")
async def create_session(
    data: AgentSessionCreate,
    context: AppContext = AuthContextDep
):
    service = AgentSessionService(context)
    session = await service.create_session(data, context.actor)
    return JsonResponse(data=session)

@router.get("/sessions", response_model=JsonResponse[List[AgentSessionRead]], summary="List agent sessions")
async def list_sessions(
    agent_instance_uuid: str = Query(..., description="Agent Instance UUID"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    context: AppContext = AuthContextDep
):
    service = AgentSessionService(context)
    sessions = await service.list_sessions(agent_instance_uuid, page, limit, context.actor)
    return JsonResponse(data=sessions)

@router.delete("/sessions/{session_uuid}", response_model=MsgResponse, summary="Archive an agent session")
async def delete_session(
    session_uuid: str,
    context: AppContext = AuthContextDep
):
    service = AgentSessionService(context)
    await service.delete_session(session_uuid, context.actor)
    return MsgResponse(msg="Session archived.")

@router.patch("/sessions/{session_uuid}", response_model=JsonResponse[AgentSessionRead], summary="Rename agent session")
async def rename_session(
    session_uuid: str,
    data: AgentSessionUpdate,
    context: AppContext = AuthContextDep,
):
    title = (data.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Session title is required.")
    service = AgentSessionService(context)
    try:
        session = await service.rename_session(session_uuid, title, context.actor)
    except (NotFoundError, PermissionDeniedError) as error:
        raise HTTPException(status_code=404 if isinstance(error, NotFoundError) else 403, detail=str(error))
    return JsonResponse(data=session)

@router.get("/sessions/{session_uuid}/messages", response_model=JsonResponse[List[AgentMessageRead]], summary="Get agent session history")
async def get_session_history(
    session_uuid: str,
    cursor: int = Query(0, description="Last message ID for pagination (0 for latest)"),
    limit: int = Query(20, ge=1, le=100),
    context: AppContext = AuthContextDep
):
    service = AgentSessionService(context)
    messages = await service.get_session_history(session_uuid, cursor, limit, context.actor)
    return JsonResponse(data=messages)

@router.post("/sessions/{session_uuid}/clear", response_model=MsgResponse, summary="Clear context messages")
async def clear_session_context(
    session_uuid: str,
    req: AgentSessionClearContextRequest,
    context: AppContext = AuthContextDep
):
    """
    清空会话的上下文记忆。
    - mode='production' (default): 软删除消息，保留审计记录。
    - mode='debug': 物理删除消息，彻底重置。
    """
    service = AgentSessionService(context)
    await service.clear_context(session_uuid, req.mode, context.actor)
    return MsgResponse(msg="Context cleared.")
