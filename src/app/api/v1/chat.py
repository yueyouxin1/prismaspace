# src/app/api/v1/chat.py

from fastapi import APIRouter, Query, status, HTTPException
from typing import List
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep
from app.schemas.common import JsonResponse, MsgResponse
from app.schemas.interaction.chat_schemas import (
    ChatSessionCreate, ChatSessionRead, ChatMessageRead, ContextClearRequest
)
from app.services.resource.agent.session_service import SessionService
from app.services.exceptions import NotFoundError, PermissionDeniedError

router = APIRouter()

@router.post("/sessions", response_model=JsonResponse[ChatSessionRead], status_code=status.HTTP_201_CREATED, summary="Create a new chat session")
async def create_session(
    data: ChatSessionCreate,
    context: AppContext = AuthContextDep
):
    service = SessionService(context)
    session = await service.create_session(data, context.actor)
    return JsonResponse(data=session)

@router.get("/sessions", response_model=JsonResponse[List[ChatSessionRead]], summary="List chat sessions for an agent")
async def list_sessions(
    agent_instance_uuid: str = Query(..., description="Agent Instance UUID"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    context: AppContext = AuthContextDep
):
    service = SessionService(context)
    sessions = await service.list_sessions(agent_instance_uuid, page, limit, context.actor)
    return JsonResponse(data=sessions)

@router.delete("/sessions/{session_uuid}", response_model=MsgResponse, summary="Delete (Archive) a chat session")
async def delete_session(
    session_uuid: str,
    context: AppContext = AuthContextDep
):
    service = SessionService(context)
    await service.delete_session(session_uuid, context.actor)
    return MsgResponse(msg="Session archived.")

@router.get("/sessions/{session_uuid}/messages", response_model=JsonResponse[List[ChatMessageRead]], summary="Get message history")
async def get_session_history(
    session_uuid: str,
    cursor: int = Query(0, description="Last message ID for pagination (0 for latest)"),
    limit: int = Query(20, ge=1, le=100),
    context: AppContext = AuthContextDep
):
    service = SessionService(context)
    messages = await service.get_session_history(session_uuid, cursor, limit, context.actor)
    return JsonResponse(data=messages)

@router.post("/sessions/{session_uuid}/clear", response_model=MsgResponse, summary="Clear context messages")
async def clear_session_context(
    session_uuid: str,
    req: ContextClearRequest,
    context: AppContext = AuthContextDep
):
    """
    清空会话的上下文记忆。
    - mode='production' (default): 软删除消息，保留审计记录。
    - mode='debug': 物理删除消息，彻底重置。
    """
    service = SessionService(context)
    await service.clear_context(session_uuid, req.mode, context.actor)
    return MsgResponse(msg="Context cleared.")