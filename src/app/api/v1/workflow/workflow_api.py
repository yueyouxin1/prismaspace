# src/app/api/v1/workflow.py

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from fastapi.responses import StreamingResponse

from app.api.dependencies.context import AuthContextDep
from app.api.dependencies.ws_auth import AuthContext, get_ws_auth
from app.core.context import AppContext
from app.schemas.common import JsonResponse, MsgResponse
from app.schemas.resource.workflow.workflow_schemas import (
    WorkflowEventRead,
    WorkflowExecutionRequest,
    WorkflowExecutionResponse,
    WorkflowNodeDefRead,
    WorkflowRead,
    WorkflowRunRead,
    WorkflowRunSummaryRead,
    WorkflowUpdate,
)
from app.services.exceptions import ServiceException
from app.services.resource.workflow.protocol_bridge import WorkflowProtocolBridgeService
from app.services.resource.workflow.workflow_service import WorkflowService
from .ws_handler import WorkflowSessionHandler

router = APIRouter()


@router.get("/nodes", response_model=JsonResponse[List[WorkflowNodeDefRead]])
async def list_node_definitions(context: AppContext = AuthContextDep):
    service = WorkflowService(context)
    nodes = await service.list_node_defs()
    return JsonResponse(data=nodes)


@router.post("/{uuid}/execute", response_model=JsonResponse[WorkflowExecutionResponse], summary="Blocking Execution")
async def execute_workflow(
    uuid: str,
    request: WorkflowExecutionRequest,
    context: AppContext = AuthContextDep,
):
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


@router.post("/{uuid}/nodes/{node_id}/debug/sse", summary="Streaming Debug Execute Node (Workflow Runtime Protocol SSE)")
async def debug_workflow_node_stream(
    uuid: str,
    node_id: str,
    request: WorkflowExecutionRequest,
    context: AppContext = AuthContextDep,
):
    service = WorkflowService(context)
    bridge = WorkflowProtocolBridgeService(service)
    try:
        stream = await bridge.debug_stream(
            instance_uuid=uuid,
            node_id=node_id,
            request=request,
            actor=context.actor,
        )
    except ServiceException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async def sse_generator():
        async for chunk in bridge.iter_sse(stream):
            yield chunk

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{uuid}/runs", response_model=JsonResponse[List[WorkflowRunSummaryRead]])
async def list_workflow_runs(
    uuid: str,
    limit: int = 20,
    context: AppContext = AuthContextDep,
):
    service = WorkflowService(context)
    result = await service.list_runs(uuid, limit=limit)
    return JsonResponse(data=result)


@router.post("/{uuid}/sse", summary="Streaming Execution (Workflow Runtime Protocol SSE)")
async def stream_workflow(
    uuid: str,
    request: WorkflowExecutionRequest,
    context: AppContext = AuthContextDep,
):
    service = WorkflowService(context)
    bridge = WorkflowProtocolBridgeService(service)
    try:
        stream = await bridge.execute_stream(
            instance_uuid=uuid,
            request=request,
            actor=context.actor,
        )
    except ServiceException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    async def sse_generator():
        async for chunk in bridge.iter_sse(stream):
            yield chunk

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.websocket("/ws")
async def websocket_workflow(
    websocket: WebSocket,
    auth_context: AuthContext = Depends(get_ws_auth),
):
    handler = WorkflowSessionHandler(websocket, auth_context)
    await handler.run()


@router.post("/{uuid}/validate", response_model=JsonResponse[Dict[str, Any]])
async def validate_workflow(
    uuid: str,
    context: AppContext = AuthContextDep,
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


@router.get("/runs/{run_id}/replay", summary="Replay Persisted Workflow Events (Workflow Runtime Protocol)")
async def replay_workflow_run_events(
    run_id: str,
    limit: int = 1000,
    protocol: str = "wrp",
    context: AppContext = AuthContextDep,
):
    service = WorkflowService(context)
    bridge = WorkflowProtocolBridgeService(service)
    try:
        stream = await bridge.replay_stream(run_id=run_id, limit=limit, protocol=protocol)
    except ServiceException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async def sse_generator():
        async for chunk in bridge.iter_sse(stream):
            yield chunk

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs/{run_id}/live", summary="Attach To Live Workflow Events (Workflow Runtime Protocol)")
async def stream_live_workflow_run_events(
    run_id: str,
    after_seq: int = 0,
    protocol: str = "wrp",
    context: AppContext = AuthContextDep,
):
    service = WorkflowService(context)
    bridge = WorkflowProtocolBridgeService(service)
    try:
        stream = await bridge.live_stream(run_id=run_id, after_seq=after_seq, protocol=protocol)
    except ServiceException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async def sse_generator():
        async for chunk in bridge.iter_sse(stream):
            yield chunk

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
