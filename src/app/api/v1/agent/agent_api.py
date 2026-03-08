import json

from fastapi import APIRouter, WebSocket, Depends, Body, Request
from fastapi.responses import StreamingResponse
from ag_ui.encoder import EventEncoder
from ag_ui.core import EventType, RunErrorEvent

from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep
from app.api.dependencies.ws_auth import get_ws_auth, AuthContext
from app.schemas.common import JsonResponse
from app.schemas.protocol import RunAgentInputExt, RunEventsResponse
from app.observability import observe_agent_stream_event
from app.services.resource.agent.agent_service import AgentService
from .ws_handler import AgentSessionHandler
from app.services.exceptions import ServiceException

router = APIRouter()

@router.post("/{uuid}/execute", response_model=JsonResponse[RunEventsResponse], summary="AG-UI Run (Non-stream)")
async def execute_agent(
    uuid: str,
    request: RunAgentInputExt,
    context: AppContext = AuthContextDep
):
    service = AgentService(context)
    result = await service.sync_execute(uuid, request, context.actor)
    return JsonResponse(data=result)

@router.post("/{uuid}/sse", summary="AG-UI Run (SSE)")
async def stream_agent(
    uuid: str,
    request: RunAgentInputExt = Body(...),
    context: AppContext = AuthContextDep,
    http_request: Request = None,
):
    service = AgentService(context)
    accept = http_request.headers.get("accept") if http_request else None
    encoder = EventEncoder(accept=accept)

    def _encode(event):
        if hasattr(event, "model_dump"):
            return encoder.encode(event)
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    async def sse_generator():
        run_result = None
        cancel_fn = None
        thread_id = request.thread_id
        run_id = request.run_id
        try:
            run_result = await service.async_execute(uuid, request, context.actor)
            cancel_fn = getattr(run_result, "cancel", None)
            thread_id = getattr(run_result, "thread_id", None) or thread_id
            run_id = getattr(run_result, "run_id", run_id)
            async for event in run_result.generator:
                observe_agent_stream_event(event)
                yield _encode(event)
        except GeneratorExit:
            if callable(cancel_fn):
                cancel_fn()
            raise
        except ServiceException as exc:
            yield _encode(
                RunErrorEvent(
                    type=EventType.RUN_ERROR,
                    threadId=thread_id,
                    runId=run_id,
                    code="AGENT_SERVICE_ERROR",
                    message=str(exc),
                    retriable=False,
                )
            )
        except Exception as exc:
            yield _encode(
                RunErrorEvent(
                    type=EventType.RUN_ERROR,
                    threadId=thread_id,
                    runId=run_id,
                    code="AGENT_RUNTIME_ERROR",
                    message=str(exc),
                    retriable=False,
                )
            )
        finally:
            if callable(cancel_fn):
                cancel_fn()

    return StreamingResponse(
        sse_generator(),
        media_type=encoder.get_content_type(),
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
