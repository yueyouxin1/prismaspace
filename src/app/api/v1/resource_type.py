# src/app/api/v1/resource_type.py

from fastapi import APIRouter, status, HTTPException
from typing import List

from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep, PublicContextDep
from app.schemas.common import JsonResponse, MsgResponse
from app.schemas.resource.resource_type_schemas import ResourceTypeCreate, ResourceTypeUpdate, ResourceTypeRead
from app.services.resource.resource_type_service import ResourceTypeService
from app.services.exceptions import ServiceException, NotFoundError, PermissionDeniedError

router = APIRouter()

# --- Resource Type Management ---

@router.post("", response_model=JsonResponse[ResourceTypeRead], status_code=status.HTTP_201_CREATED)
async def create_resource_type(type_in: ResourceTypeCreate, context: AppContext = AuthContextDep):
    """
    [Admin] 创建一个新的资源类型定义。
    """
    try:
        # 1. 实例化 Service
        service = ResourceTypeService(context)
        # 2. 调用 Service 方法
        new_type = await service.create_resource_type(type_in)
        # 3. 序列化并返回
        return JsonResponse(data=new_type)
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ServiceException as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("", response_model=JsonResponse[List[ResourceTypeRead]])
async def list_resource_types(context: AppContext = PublicContextDep):
    """
    [Public] 获取所有已定义的资源类型列表。
    """
    try:
        service = ResourceTypeService(context)
        types = await service.get_all_resource_types()
        return JsonResponse(data=types)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ServiceException as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/{name}", response_model=JsonResponse[ResourceTypeRead])
async def update_resource_type(name: str, update_data: ResourceTypeUpdate, context: AppContext = AuthContextDep):
    """
    [Admin] 更新一个已存在的资源类型定义。
    """
    try:
        service = ResourceTypeService(context)
        updated_type = await service.update_resource_type(name, update_data)
        return JsonResponse(data=updated_type)
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ServiceException as e:
        raise HTTPException(status_code=400, detail=str(e))
    
@router.delete("/{name}", response_model=MsgResponse)
async def delete_resource_type(name: str, context: AppContext = AuthContextDep):
    """
    [Admin] 删除一个资源类型定义。
    """
    try:
        service = ResourceTypeService(context)
        await service.delete_resource_type(name)
        return MsgResponse(msg=f"Resource type '{name}' deleted successfully.")
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ServiceException as e:
        raise HTTPException(status_code=400, detail=str(e))
