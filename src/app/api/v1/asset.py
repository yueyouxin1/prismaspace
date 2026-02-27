from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.api.dependencies.context import AuthContextDep
from app.core.context import AppContext
from app.models.asset import AssetType
from app.schemas.asset.asset_schemas import (
    AssetConfirm,
    AssetCreate,
    AssetRead,
    AssetUpdate,
    AssetUploadTicket,
    PaginatedAssetsResponse,
)
from app.schemas.asset.folder_schemas import (
    AssetFolderCreate,
    AssetFolderRead,
    AssetFolderTreeNodeRead,
    AssetFolderUpdate,
)
from app.schemas.common import JsonResponse, MsgResponse
from app.services.asset.asset_service import AssetService
from app.services.asset.folder_service import AssetFolderService
from app.services.exceptions import NotFoundError, ServiceException

router = APIRouter()


@router.post("/upload/ticket", response_model=JsonResponse[AssetUploadTicket], summary="Create Upload Ticket")
async def create_upload_ticket(
    workspace_uuid: str,
    data: AssetCreate,
    context: AppContext = AuthContextDep,
):
    service = AssetService(context)
    try:
        return JsonResponse(data=await service.create_upload_ticket(workspace_uuid, data, context.actor))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ServiceException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/upload/confirm", response_model=JsonResponse[AssetRead], summary="Confirm Upload")
async def confirm_upload(data: AssetConfirm, context: AppContext = AuthContextDep):
    service = AssetService(context)
    try:
        return JsonResponse(data=await service.confirm_upload(data, context.actor))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ServiceException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=JsonResponse[PaginatedAssetsResponse], summary="List Assets")
async def list_assets(
    workspace_uuid: str,
    folder_uuid: Optional[str] = Query(None),
    folder_id: Optional[int] = Query(None),
    include_subfolders: bool = Query(False),
    type: Optional[AssetType] = Query(None),
    keyword: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    context: AppContext = AuthContextDep,
):
    service = AssetService(context)
    try:
        result = await service.list_assets(
            workspace_uuid=workspace_uuid,
            actor=context.actor,
            folder_uuid=folder_uuid,
            folder_id=folder_id,
            include_subfolders=include_subfolders,
            asset_type=type,
            keyword=keyword,
            page=page,
            limit=limit,
        )
        return JsonResponse(data=result)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ServiceException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/folders", response_model=JsonResponse[AssetFolderRead], summary="Create Folder")
async def create_folder(
    workspace_uuid: str,
    data: AssetFolderCreate,
    context: AppContext = AuthContextDep,
):
    service = AssetFolderService(context)
    try:
        return JsonResponse(data=await service.create_folder(workspace_uuid, data, context.actor))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ServiceException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/folders", response_model=JsonResponse[List[AssetFolderRead]], summary="List Folders")
async def list_folders(
    workspace_uuid: str,
    parent_uuid: Optional[str] = Query(None),
    parent_id: Optional[int] = Query(None),
    context: AppContext = AuthContextDep,
):
    service = AssetFolderService(context)
    try:
        folders = await service.list_folders(workspace_uuid, context.actor, parent_uuid, parent_id)
        return JsonResponse(data=folders)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ServiceException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/folders/tree", response_model=JsonResponse[List[AssetFolderTreeNodeRead]], summary="Folder Tree")
async def list_folder_tree(workspace_uuid: str, context: AppContext = AuthContextDep):
    service = AssetFolderService(context)
    try:
        return JsonResponse(data=await service.list_folder_tree(workspace_uuid, context.actor))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/folders/{folder_uuid}", response_model=JsonResponse[AssetFolderRead], summary="Patch Folder")
async def patch_folder(folder_uuid: str, data: AssetFolderUpdate, context: AppContext = AuthContextDep):
    service = AssetFolderService(context)
    try:
        return JsonResponse(data=await service.update_folder(folder_uuid, data, context.actor))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ServiceException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/folders/{folder_uuid}", response_model=MsgResponse, summary="Delete Folder")
async def delete_folder(folder_uuid: str, context: AppContext = AuthContextDep):
    service = AssetFolderService(context)
    try:
        await service.delete_folder(folder_uuid, context.actor)
        return MsgResponse(msg="Folder deleted.")
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ServiceException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{asset_uuid}", response_model=JsonResponse[AssetRead], summary="Get Asset")
async def get_asset(asset_uuid: str, context: AppContext = AuthContextDep):
    service = AssetService(context)
    try:
        return JsonResponse(data=await service.get_asset(asset_uuid, context.actor))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{asset_uuid}", response_model=JsonResponse[AssetRead], summary="Patch Asset")
async def patch_asset(asset_uuid: str, data: AssetUpdate, context: AppContext = AuthContextDep):
    service = AssetService(context)
    try:
        return JsonResponse(data=await service.update_asset(asset_uuid, data, context.actor))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ServiceException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{asset_uuid}", response_model=MsgResponse, summary="Delete Asset")
async def delete_asset(asset_uuid: str, context: AppContext = AuthContextDep):
    service = AssetService(context)
    try:
        await service.delete_asset(asset_uuid, context.actor)
        return MsgResponse(msg="Asset deleted.")
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
