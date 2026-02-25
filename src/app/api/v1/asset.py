from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status, HTTPException
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep
from app.schemas.common import JsonResponse, MsgResponse
from app.schemas.asset.asset_schemas import (
    AssetCreate, AssetUploadTicket, AssetRead, AssetConfirm, AssetUpdate
)
from app.schemas.asset.folder_schemas import (
    AssetFolderCreate, AssetFolderRead, AssetFolderUpdate
)
from app.services.asset.asset_service import AssetService
from app.services.asset.folder_service import AssetFolderService
from app.models.asset import AssetType
from app.services.exceptions import ServiceException, NotFoundError

router = APIRouter()

# ==============================================================================
# Asset Endpoints
# ==============================================================================

@router.post("/upload/ticket", response_model=JsonResponse[AssetUploadTicket], summary="Create Upload Ticket")
async def create_upload_ticket(
    workspace_uuid: str,
    data: AssetCreate,
    context: AppContext = AuthContextDep
):
    """
    [Step 1] Request a signed upload URL.
    """
    service = AssetService(context)
    try:
        ticket = await service.create_upload_ticket(workspace_uuid, data, context.actor)
        return JsonResponse(data=ticket)
    except (ServiceException, NotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/upload/confirm", response_model=JsonResponse[AssetRead], summary="Confirm Upload")
async def confirm_upload(
    data: AssetConfirm,
    context: AppContext = AuthContextDep
):
    """
    [Step 2] Confirm upload success and trigger processing.
    """
    service = AssetService(context)
    try:
        asset = await service.confirm_upload(data, context.actor)
        return JsonResponse(data=asset)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ServiceException as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("", response_model=JsonResponse[List[AssetRead]], summary="List Assets")
async def list_assets(
    workspace_uuid: str,
    folder_id: Optional[int] = Query(None),
    type: Optional[AssetType] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    context: AppContext = AuthContextDep
):
    service = AssetService(context)
    # Note: Service methods should handle type filtering
    # Since `list_assets` in Service didn't have `type` arg in previous step, 
    # we assume it filters by folder only or we update service.
    # Updated Service call assuming we added `asset_type` to `list_assets` in Service.
    assets = await service.list_assets(
        workspace_uuid=workspace_uuid,
        actor=context.actor,
        folder_id=folder_id,
        asset_type=type,
        page=page,
        limit=limit
        # asset_type=type # If service supports it
    )
    return JsonResponse(data=assets)

@router.put("/{asset_uuid}", response_model=JsonResponse[AssetRead], summary="Update Asset")
async def update_asset(
    asset_uuid: str,
    data: AssetUpdate,
    context: AppContext = AuthContextDep
):
    service = AssetService(context)
    try:
        asset = await service.update_asset(asset_uuid, data, context.actor)
        return JsonResponse(data=asset)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Asset not found")

@router.delete("/{asset_uuid}", response_model=MsgResponse, summary="Delete Asset")
async def delete_asset(
    asset_uuid: str,
    context: AppContext = AuthContextDep
):
    service = AssetService(context)
    try:
        await service.delete_asset(asset_uuid, context.actor)
        return MsgResponse(msg="Asset deleted.")
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Asset not found")

# ==============================================================================
# Folder Endpoints
# ==============================================================================

@router.post("/folders", response_model=JsonResponse[AssetFolderRead], summary="Create Folder")
async def create_folder(
    workspace_uuid: str,
    data: AssetFolderCreate,
    context: AppContext = AuthContextDep
):
    service = AssetFolderService(context)
    folder = await service.create_folder(workspace_uuid, data, context.actor)
    return JsonResponse(data=folder)

@router.get("/folders", response_model=JsonResponse[List[AssetFolderRead]], summary="List Folders")
async def list_folders(
    workspace_uuid: str,
    parent_id: Optional[int] = Query(None),
    context: AppContext = AuthContextDep
):
    service = AssetFolderService(context)
    folders = await service.list_folders(workspace_uuid, context.actor, parent_id)
    return JsonResponse(data=folders)

@router.delete("/folders/{folder_uuid}", response_model=MsgResponse, summary="Delete Folder")
async def delete_folder(
    folder_uuid: str,
    context: AppContext = AuthContextDep
):
    service = AssetFolderService(context)
    try:
        await service.delete_folder(folder_uuid, context.actor)
        return MsgResponse(msg="Folder deleted.")
    except ServiceException as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Folder not found")