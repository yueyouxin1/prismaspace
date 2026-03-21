# src/app/api/v1/workflow.py

import asyncio
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
from app.services.resource.workflow.protocol_adapter import WorkflowRuntimeProtocolAdapter
from app.services.resource.workflow.workflow_service import WorkflowService
from .ws_handler import WorkflowSessionHandler

router = APIRouter()


def _build_node_index(graph: Dict[str, Any] | None) -> Dict[str, Dict[str, str]]:
    node_index: Dict[str, Dict[str, str]] = {}
    if not isinstance(graph, dict):
        return node_index
    for node in graph.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        node_data = node.get("data") or {}
        if not isinstance(node_id, str) or not isinstance(node_data, dict):
            continue
        node_index[node_id] = {
            "registryId": str(node_data.get("registryId") or ""),
            "name": str(node_data.get("name") or node_id),
        }
    return node_index


async def _load_node_index_for_instance(service: WorkflowService, instance_uuid: str) -> Dict[str, Dict[str, str]]:
    instance = await service.get_by_uuid(instance_uuid)
    if not instance:
        return {}
    await service._check_execute_perm(instance)
    return _build_node_index(instance.graph or {})


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
    debug_request = await service.build_debug_node_request(
        instance_uuid=uuid,
        node_id=node_id,
        execute_params=request,
    )
    adapter = WorkflowRuntimeProtocolAdapter()
    node_index = await _load_node_index_for_instance(service, uuid)

    async def sse_generator():
        result = None
        detach_fn = None
        detached = False
        result = await service.async_execute(uuid, debug_request, context.actor)
        detach_fn = getattr(result, "detach", None)
        try:
            yield adapter.to_sse(
                adapter.build_session_ready(
                    run_id=result.run_id,
                    thread_id=result.thread_id,
                    trace_id=result.trace_id,
                    parent_run_id=debug_request.parent_run_id,
                    mode="debug",
                )
            )
            async for event in result.generator:
                seq = None
                try:
                    seq = int(event.id) if event.id is not None else None
                except Exception:
                    seq = None
                envelope = adapter.build_envelope(
                    event_type=event.event,
                    payload=event.data,
                    run_id=result.run_id,
                    thread_id=result.thread_id,
                    trace_id=result.trace_id,
                    parent_run_id=debug_request.parent_run_id,
                    seq=seq,
                    node_index=node_index,
                )
                yield adapter.to_sse(envelope)
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
    adapter = WorkflowRuntimeProtocolAdapter()
    node_index = await _load_node_index_for_instance(service, uuid)

    async def sse_generator():
        result = None
        detach_fn = None
        detached = False
        result = await service.async_execute(uuid, request, context.actor)
        detach_fn = getattr(result, "detach", None)
        try:
            yield adapter.to_sse(
                adapter.build_session_ready(
                    run_id=result.run_id,
                    thread_id=result.thread_id,
                    trace_id=result.trace_id,
                    parent_run_id=request.parent_run_id,
                    mode="execute",
                )
            )
            async for event in result.generator:
                seq = None
                try:
                    seq = int(event.id) if event.id is not None else None
                except Exception:
                    seq = None
                envelope = adapter.build_envelope(
                    event_type=event.event,
                    payload=event.data,
                    run_id=result.run_id,
                    thread_id=result.thread_id,
                    trace_id=result.trace_id,
                    parent_run_id=request.parent_run_id,
                    seq=seq,
                    node_index=node_index,
                )
                yield adapter.to_sse(envelope)
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
    context: AppContext = AuthContextDep,
):
    service = WorkflowService(context)
    adapter = WorkflowRuntimeProtocolAdapter()
    run = await service.get_run(run_id)
    node_index = await _load_node_index_for_instance(service, run.workflow_instance_uuid)

    async def sse_generator():
        events = await service.list_run_events(run_id, limit=limit)
        yield adapter.to_sse(
            adapter.build_session_ready(
                run_id=run.run_id,
                thread_id=run.thread_id,
                trace_id=run.trace_id,
                parent_run_id=run.parent_run_id,
                mode="replay",
            )
        )
        for event in events:
            envelope = adapter.build_envelope(
                event_type=event.event_type,
                payload=event.payload,
                run_id=run.run_id,
                thread_id=run.thread_id,
                trace_id=run.trace_id,
                parent_run_id=run.parent_run_id,
                seq=event.sequence_no,
                ts=event.created_at,
                node_index=node_index,
            )
            yield adapter.to_sse(envelope)
        yield adapter.to_sse(
            adapter.build_replay_completed(
                run_id=run.run_id,
                thread_id=run.thread_id,
                trace_id=run.trace_id,
                parent_run_id=run.parent_run_id,
                count=len(events),
                limit=limit,
            )
        )

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
    context: AppContext = AuthContextDep,
):
    service = WorkflowService(context)
    adapter = WorkflowRuntimeProtocolAdapter()
    run = await service.get_run(run_id)
    node_index = await _load_node_index_for_instance(service, run.workflow_instance_uuid)

    async def sse_generator():
        yield adapter.to_sse(
            adapter.build_session_ready(
                run_id=run.run_id,
                thread_id=run.thread_id,
                trace_id=run.trace_id,
                parent_run_id=run.parent_run_id,
                mode="live",
            )
        )
        yield adapter.to_sse(
            adapter.build_run_attached(
                run_id=run.run_id,
                thread_id=run.thread_id,
                trace_id=run.trace_id,
                parent_run_id=run.parent_run_id,
                after_seq=after_seq,
            )
        )
        async for live_envelope in service.stream_live_run_events(run_id, after_seq=after_seq):
            payload = live_envelope.get("payload", {})
            envelope = adapter.build_envelope(
                event_type=str(payload.get("event", "message")),
                payload=payload.get("data", {}) if isinstance(payload.get("data"), dict) else {"value": payload.get("data")},
                run_id=run.run_id,
                thread_id=run.thread_id,
                trace_id=run.trace_id,
                parent_run_id=run.parent_run_id,
                seq=int(live_envelope.get("seq", 0)) if live_envelope.get("seq") is not None else None,
                node_index=node_index,
            )
            yield adapter.to_sse(envelope)

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
