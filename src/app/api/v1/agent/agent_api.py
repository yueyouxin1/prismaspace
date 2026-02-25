# src/app/api/v1/agent.py

from fastapi import APIRouter, WebSocket, Depends, Body, HTTPException, status
from fastapi.responses import StreamingResponse
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep
from app.api.dependencies.ws_auth import get_ws_auth, AuthContext
from app.schemas.common import JsonResponse
from app.schemas.resource.agent.agent_schemas import AgentExecutionRequest, AgentExecutionResponse, AgentRead
from app.services.resource.agent.agent_service import AgentService
from .ws_handler import AgentSessionHandler
from app.services.exceptions import ServiceException

router = APIRouter()

@router.post("/{uuid}/execute", response_model=JsonResponse[AgentExecutionResponse], summary="Blocking Execution")
async def execute_agent(
    uuid: str,
    request: AgentExecutionRequest,
    context: AppContext = AuthContextDep
):
    service = AgentService(context)
    result = await service.execute(uuid, request, context.actor)
    # result is ExecutionResult, wrap it
    return JsonResponse(data=result)

@router.post("/{uuid}/sse", summary="Streaming Execution (SSE)")
async def stream_agent(
    uuid: str,
    request: AgentExecutionRequest = Body(...),
    context: AppContext = AuthContextDep
):
    service = AgentService(context)

    async def sse_generator():
        result = await service.async_execute(uuid, request, context.actor)
        async for event in result.generator:
            yield event.to_sse()

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

@router.websocket("/chat")
async def websocket_agent_chat(
    websocket: WebSocket,
    auth_context: AuthContext = Depends(get_ws_auth),
):
    handler = AgentSessionHandler(websocket, auth_context)
    await handler.run()