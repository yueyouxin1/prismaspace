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
from app.schemas.resource.agent.agent_schemas import AgentRunDetailRead, AgentRunEventRead, AgentRunSummaryRead
from app.observability import observe_agent_stream_event
from app.services.resource.agent.agent_service import AgentService
from .ws_handler import AgentSessionHandler
from app.services.exceptions import ActiveRunExistsError, ServiceException

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


@router.get("/{uuid}/runs", response_model=JsonResponse[list[AgentRunSummaryRead]], summary="List Agent Runs")
async def list_agent_runs(
    uuid: str,
    limit: int = 20,
    context: AppContext = AuthContextDep,
):
    service = AgentService(context)
    result = await service.list_runs(uuid, limit=limit)
    return JsonResponse(data=result)


@router.get("/{uuid}/active-run", response_model=JsonResponse[AgentRunSummaryRead | None], summary="Get Active Agent Run By Thread")
async def get_active_agent_run(
    uuid: str,
    thread_id: str,
    context: AppContext = AuthContextDep,
):
    service = AgentService(context)
    result = await service.get_active_run(uuid, context.actor, thread_id)
    return JsonResponse(data=result)


@router.get("/runs/{run_id}", response_model=JsonResponse[AgentRunDetailRead], summary="Get Agent Run")
async def get_agent_run(
    run_id: str,
    context: AppContext = AuthContextDep,
):
    service = AgentService(context)
    result = await service.get_run(run_id)
    return JsonResponse(data=result)


@router.get("/runs/{run_id}/events", response_model=JsonResponse[list[AgentRunEventRead]], summary="List Agent Run Events")
async def list_agent_run_events(
    run_id: str,
    limit: int = 1000,
    context: AppContext = AuthContextDep,
):
    service = AgentService(context)
    result = await service.list_run_events(run_id, limit=limit)
    return JsonResponse(data=result)


@router.get("/runs/{run_id}/replay", summary="Replay Persisted Agent Events")
async def replay_agent_run_events(
    run_id: str,
    limit: int = 1000,
    context: AppContext = AuthContextDep,
):
    service = AgentService(context)
    encoder = EventEncoder(accept="text/event-stream")

    async def sse_generator():
        events = await service.list_run_events(run_id, limit=limit)
        for event in events:
            payload = event.payload
            if hasattr(payload, "model_dump_json"):
                yield encoder.encode(payload)
            else:
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        sse_generator(),
        media_type=encoder.get_content_type(),
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs/{run_id}/live", summary="Attach To Live Agent Events")
async def stream_live_agent_run_events(
    run_id: str,
    after_seq: int = 0,
    context: AppContext = AuthContextDep,
):
    service = AgentService(context)

    async def sse_generator():
        async for envelope in service.stream_live_run_events(run_id, after_seq=after_seq):
            payload = envelope.get("payload", {})
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/runs/{run_id}/cancel", response_model=JsonResponse[dict], summary="Cancel Agent Run")
async def cancel_agent_run(
    run_id: str,
    context: AppContext = AuthContextDep,
):
    service = AgentService(context)
    result = await service.cancel_run(run_id)
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
        detach_fn = None
        detached = False
        thread_id = request.thread_id
        run_id = request.run_id
        try:
            run_result = await service.async_execute(uuid, request, context.actor)
            cancel_fn = getattr(run_result, "cancel", None)
            detach_fn = getattr(run_result, "detach", None)
            thread_id = getattr(run_result, "thread_id", None) or thread_id
            run_id = getattr(run_result, "run_id", run_id)
            async for event in run_result.generator:
                observe_agent_stream_event(event)
                yield _encode(event)
        except ActiveRunExistsError as exc:
            yield _encode(
                RunErrorEvent(
                    type=EventType.RUN_ERROR,
                    threadId=thread_id,
                    runId=run_id,
                    code="AGENT_ACTIVE_RUN_EXISTS",
                    message=str(exc),
                    retriable=True,
                )
            )
        except GeneratorExit:
            detached = True
            if callable(detach_fn):
                detach_fn()
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
            if callable(cancel_fn) and not detached:
                cancel_fn()
            if run_result and getattr(run_result, "task", None) and not detached:
                try:
                    await run_result.task
                except Exception:
                    pass

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
