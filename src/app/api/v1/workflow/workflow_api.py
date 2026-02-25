# src/app/api/v1/workflow.py

from fastapi import APIRouter, Depends, Body, status, HTTPException, WebSocket
from fastapi.responses import StreamingResponse
from typing import List, Dict, Any
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep
from app.api.dependencies.ws_auth import get_ws_auth, AuthContext
from app.schemas.common import JsonResponse, MsgResponse
from app.schemas.resource.workflow.workflow_schemas import (
    WorkflowNodeDefRead, WorkflowRead, WorkflowUpdate, WorkflowExecutionRequest, 
    WorkflowExecutionResponse, WorkflowEvent
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
        result = await service.async_execute(uuid, request, context.actor)
        try:
            async for event in result.generator:
                yield event.to_sse()
        finally:
            if result.task and not result.task.done():
                result.task.cancel()
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
