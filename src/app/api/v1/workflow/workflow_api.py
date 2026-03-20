# src/app/api/v1/workflow.py

import asyncio

from fastapi import APIRouter, Depends, Body, status, HTTPException, WebSocket
from fastapi.responses import StreamingResponse
from typing import List, Dict, Any
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep
from app.api.dependencies.ws_auth import get_ws_auth, AuthContext
from app.schemas.common import JsonResponse, MsgResponse
from app.schemas.resource.workflow.workflow_schemas import (
    WorkflowNodeDefRead, WorkflowRead, WorkflowUpdate, WorkflowExecutionRequest, 
    WorkflowExecutionResponse, WorkflowEvent, WorkflowEventRead, WorkflowRunRead, WorkflowRunSummaryRead
)
from app.services.resource.workflow.workflow_service import WorkflowService
from app.services.exceptions import ServiceException
from .ws_handler import WorkflowSessionHandler

router = APIRouter()

@router.get("/nodes", response_model=JsonResponse[List[WorkflowNodeDefRead]])
async def list_node_definitions(context: AppContext = AuthContextDep):
    """
    获取所有可用的工作流节点定义，用于前端渲染组件面板。
    """
    service = WorkflowService(context)
    nodes = await service.list_node_defs()
    return JsonResponse(data=nodes)

# --- Execution Endpoints ---

@router.post("/{uuid}/execute", response_model=JsonResponse[WorkflowExecutionResponse], summary="Blocking Execution")
async def execute_workflow(
    uuid: str,
    request: WorkflowExecutionRequest,
    context: AppContext = AuthContextDep
):
    """
    Run a workflow synchronously and wait for the final result.
    """
    service = WorkflowService(context)
    result = await service.execute(uuid, request, context.actor)
    return JsonResponse(data=result)


@router.post("/{uuid}/async", response_model=JsonResponse[WorkflowRunSummaryRead], summary="Async Execution")
async def execute_workflow_async(
    uuid: str,
    request: WorkflowExecutionRequest,
    context: AppContext = AuthContextDep,
):
    service = WorkflowService(context)
    result = await service.start_background_execute(uuid, request, context.actor)
    return JsonResponse(data=result)


@router.post("/{uuid}/nodes/{node_id}/debug", response_model=JsonResponse[WorkflowExecutionResponse], summary="Debug Execute Node")
async def debug_workflow_node(
    uuid: str,
    node_id: str,
    request: WorkflowExecutionRequest,
    context: AppContext = AuthContextDep,
):
    service = WorkflowService(context)
    result = await service.debug_node_execute(uuid, node_id, request, context.actor)
    return JsonResponse(data=result)


@router.get("/{uuid}/runs", response_model=JsonResponse[List[WorkflowRunSummaryRead]])
async def list_workflow_runs(
    uuid: str,
    limit: int = 20,
    context: AppContext = AuthContextDep,
):
    service = WorkflowService(context)
    result = await service.list_runs(uuid, limit=limit)
    return JsonResponse(data=result)

@router.post("/{uuid}/sse", summary="Streaming Execution (SSE)")
async def stream_workflow(
    uuid: str,
    request: WorkflowExecutionRequest,
    context: AppContext = AuthContextDep
):
    """
    Run a workflow and stream events via Server-Sent Events.
    """
    service = WorkflowService(context)
    
    async def sse_generator():
        result = None
        detach_fn = None
        detached = False
        result = await service.async_execute(uuid, request, context.actor)
        detach_fn = getattr(result, "detach", None)
        try:
            async for event in result.generator:
                yield event.to_sse()
        except GeneratorExit:
            detached = True
            if callable(detach_fn):
                detach_fn()
            raise
        except asyncio.CancelledError:
            detached = True
            if callable(detach_fn):
                detach_fn()
            raise
        finally:
            if result and result.task and not detached:
                try:
                    await result.task
                except Exception:
                    pass

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

# --- WebSocket Endpoint ---

@router.websocket("/ws")
async def websocket_workflow(
    websocket: WebSocket,
    auth_context: AuthContext = Depends(get_ws_auth),
):
    handler = WorkflowSessionHandler(websocket, auth_context)
    await handler.run()

# --- Management Endpoints (Delegated to ResourceService for common ops, but handled here for specifics) ---

# Note: Standard CRUD (Create, Update, Delete) is handled by the generic Resource router via ResourceService.
# However, if we need specific workflow endpoints (like 'validate'), add them here.

@router.post("/{uuid}/validate", response_model=JsonResponse[Dict[str, Any]])
async def validate_workflow(
    uuid: str,
    context: AppContext = AuthContextDep
):
    service = WorkflowService(context)
    instance = await service.get_by_uuid(uuid)
    if not instance:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    result = await service.validate_instance(instance)
    return JsonResponse(data={"is_valid": result.is_valid, "errors": result.errors})


@router.get("/runs/{run_id}", response_model=JsonResponse[WorkflowRunRead])
async def get_workflow_run(
    run_id: str,
    context: AppContext = AuthContextDep,
):
    service = WorkflowService(context)
    result = await service.get_run(run_id)
    return JsonResponse(data=result)


@router.get("/runs/{run_id}/events", response_model=JsonResponse[List[WorkflowEventRead]])
async def list_workflow_run_events(
    run_id: str,
    limit: int = 1000,
    context: AppContext = AuthContextDep,
):
    service = WorkflowService(context)
    result = await service.list_run_events(run_id, limit=limit)
    return JsonResponse(data=result)


@router.get("/runs/{run_id}/replay", summary="Replay Persisted Workflow Events")
async def replay_workflow_run_events(
    run_id: str,
    limit: int = 1000,
    context: AppContext = AuthContextDep,
):
    service = WorkflowService(context)

    async def sse_generator():
        events = await service.list_run_events(run_id, limit=limit)
        for event in events:
            yield WorkflowEvent(event=event.event_type, data=event.payload).to_sse()

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs/{run_id}/live", summary="Attach To Live Workflow Events")
async def stream_live_workflow_run_events(
    run_id: str,
    after_seq: int = 0,
    context: AppContext = AuthContextDep,
):
    service = WorkflowService(context)

    async def sse_generator():
        async for envelope in service.stream_live_run_events(run_id, after_seq=after_seq):
            payload = envelope.get("payload", {})
            seq = envelope.get("seq")
            event_name = str(payload.get("event", "message"))
            data = payload.get("data", {})
            yield WorkflowEvent(
                id=str(seq) if seq is not None else None,
                event=event_name,
                data=data if isinstance(data, dict) else {"value": data},
            ).to_sse()

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/runs/{run_id}/cancel", response_model=JsonResponse[Dict[str, Any]])
async def cancel_workflow_run(
    run_id: str,
    context: AppContext = AuthContextDep,
):
    service = WorkflowService(context)
    result = await service.cancel_run(run_id)
    return JsonResponse(data=result)
