# src/app/api/v1/agent.py

from fastapi import APIRouter, WebSocket, Depends, Body
from fastapi.responses import StreamingResponse
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep
from app.api.dependencies.ws_auth import get_ws_auth, AuthContext
from app.schemas.common import JsonResponse
from app.schemas.resource.agent.agent_schemas import AgentExecutionRequest, AgentExecutionResponse
from app.schemas.protocol import AgUiRunAgentInput
from app.services.resource.agent.agent_service import AgentService
from app.services.resource.agent.ag_ui_adapter import AgUiAgentAdapter, encode_sse_data
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

@router.post("/{uuid}/sse", summary="AG-UI Run (SSE)")
async def stream_agent(
    uuid: str,
    request: AgUiRunAgentInput = Body(...),
    context: AppContext = AuthContextDep
):
    service = AgentService(context)
    adapter = AgUiAgentAdapter(service)

    async def sse_generator():
        try:
            async for event in adapter.stream_events(uuid, request, context.actor):
                yield encode_sse_data(event)
        except ServiceException as exc:
            yield encode_sse_data({
                "type": "RUN_ERROR",
                "threadId": request.thread_id,
                "runId": request.run_id,
                "code": "AGENT_SERVICE_ERROR",
                "message": str(exc),
                "retriable": False,
            })
        except Exception as exc:
            yield encode_sse_data({
                "type": "RUN_ERROR",
                "threadId": request.thread_id,
                "runId": request.run_id,
                "code": "AGENT_RUNTIME_ERROR",
                "message": str(exc),
                "retriable": False,
            })

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
