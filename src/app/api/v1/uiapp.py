# src/app/api/v1/uiapp.py

from fastapi import APIRouter, status, HTTPException
from typing import List
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep
from app.schemas.common import JsonResponse, MsgResponse
from app.schemas.resource.uiapp.uiapp_schemas import UiPageCreate, UiPageUpdate, UiPageDetail, UiPageMeta
from app.services.resource.uiapp.uiapp_service import UiAppService
from app.services.exceptions import ServiceException, NotFoundError

router = APIRouter()

@router.get("/{app_uuid}/pages/{page_uuid}", response_model=JsonResponse[UiPageDetail])
async def get_page_detail(
    app_uuid: str,
    page_uuid: str,
    context: AppContext = AuthContextDep
):
    """[Lazy Load] 获取特定页面的完整 DSL"""
    service = UiAppService(context)
    try:
        page = await service.get_page_detail(app_uuid, page_uuid, context.actor)
        return JsonResponse(data=page)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/{app_uuid}/pages", response_model=JsonResponse[UiPageMeta])
async def create_page(
    app_uuid: str,
    page_data: UiPageCreate,
    context: AppContext = AuthContextDep
):
    """创建新页面"""
    service = UiAppService(context)
    try:
        page = await service.create_page(app_uuid, page_data, context.actor)
        return JsonResponse(data=page)
    except ServiceException as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/{app_uuid}/pages/{page_uuid}", response_model=JsonResponse[UiPageDetail])
async def update_page(
    app_uuid: str,
    page_uuid: str,
    update_data: UiPageUpdate,
    context: AppContext = AuthContextDep
):
    """更新页面 (DSL 或 配置)"""
    service = UiAppService(context)
    try:
        page = await service.update_page(app_uuid, page_uuid, update_data, context.actor)
        return JsonResponse(data=page)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.delete("/{app_uuid}/pages/{page_uuid}", response_model=MsgResponse)
async def delete_page(
    app_uuid: str,
    page_uuid: str,
    context: AppContext = AuthContextDep
):
    """删除页面"""
    service = UiAppService(context)
    try:
        await service.delete_page(app_uuid, page_uuid, context.actor)
        return MsgResponse(msg="Page deleted")
    except ServiceException as e:
        raise HTTPException(status_code=400, detail=str(e))