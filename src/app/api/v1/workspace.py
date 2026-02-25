# app/api/v1/workspace.py

from fastapi import APIRouter, Depends, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep
from app.schemas.common import JsonResponse, MsgResponse
from app.schemas.workspace.workspace_schemas import WorkspaceRead, WorkspaceCreate, WorkspaceUpdate
from app.services.workspace.workspace_service import WorkspaceService
from app.services.exceptions import PermissionDeniedError, NotFoundError, ServiceException

router = APIRouter()

@router.get("", response_model=JsonResponse[List[WorkspaceRead]], summary="List Workspaces")
async def list_workspaces(
    context: AppContext = AuthContextDep
):
    """获取当前用户有权访问的所有工作空间列表。"""
    service = WorkspaceService(context)
    workspaces_data = await service.list_workspaces(actor=context.actor)
    return JsonResponse(data=workspaces_data)

@router.post("", response_model=JsonResponse[WorkspaceRead], status_code=status.HTTP_201_CREATED, summary="Create Workspace for a Team")
async def create_workspace(
    workspace_in: WorkspaceCreate,
    context: AppContext = AuthContextDep
):
    """为指定团队创建一个新的工作空间。"""
    try:
        service = WorkspaceService(context)
        workspace_read_data = await service.create_workspace_for_team(workspace_data=workspace_in, actor=context.actor)
        return JsonResponse(data=workspace_read_data)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))

@router.get("/{workspace_uuid}", response_model=JsonResponse[WorkspaceRead], summary="Get Workspace Details")
async def get_workspace(
    workspace_uuid: str,
    context: AppContext = AuthContextDep
):
    """获取指定工作空间的详细信息。"""
    try:
        service = WorkspaceService(context)
        workspace_data = await service.get_workspace_by_uuid(workspace_uuid=workspace_uuid, actor=context.actor)
        return JsonResponse(data=workspace_data)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))

@router.put("/{workspace_uuid}", response_model=JsonResponse[WorkspaceRead], summary="Update Workspace")
async def update_workspace(
    workspace_uuid: str,
    workspace_in: WorkspaceUpdate,
    context: AppContext = AuthContextDep
):
    """更新工作空间的名称或头像。"""
    try:
        service = WorkspaceService(context)
        workspace_read_data = await service.update_workspace_by_uuid(
            workspace_uuid=workspace_uuid, 
            update_data=workspace_in, 
            actor=context.actor
        )
        return JsonResponse(data=workspace_read_data)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))

@router.delete("/{workspace_uuid}", response_model=MsgResponse, summary="Archive Workspace (Soft Delete)")
async def archive_workspace(
    workspace_uuid: str,
    context: AppContext = AuthContextDep
):
    """归档一个工作空间，这是一个软删除操作。"""
    try:
        service = WorkspaceService(context)
        await service.archive_workspace_by_uuid(workspace_uuid=workspace_uuid, actor=context.actor)
        return MsgResponse(msg="Workspace archived successfully.")
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ServiceException as e:
        raise HTTPException(status_code=400, detail=str(e))