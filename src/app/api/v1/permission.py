# src/app/api/v1/permission.py

from fastapi import APIRouter, status, HTTPException, Path
from typing import List
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep, PublicContextDep
from app.schemas.common import JsonResponse, MsgResponse
from app.schemas.permission.permission_schemas import PermissionCreate, PermissionUpdate, PermissionReadNode
from app.services.permission.permission_service import PermissionService
from app.services.exceptions import ServiceException, NotFoundError

router = APIRouter()

@router.get("/tree", response_model=JsonResponse[List[PermissionReadNode]], summary="[Admin] Get Full Permission Tree")
async def get_permission_tree(context: AppContext = AuthContextDep): # <-- 改为 AuthContextDep
    """
    [Admin] 获取系统所有权限的完整树状结构，用于平台管理后台。
    """
    # Service层会处理具体的权限检查
    service = PermissionService(context)
    tree = await service.get_permission_tree()
    return JsonResponse(data=tree)

@router.post("", response_model=JsonResponse[PermissionReadNode], status_code=status.HTTP_201_CREATED, summary="[Admin] Create Permission")
async def create_permission(perm_in: PermissionCreate, context: AppContext = AuthContextDep):
    """
    [Admin] 创建一个新的权限定义，支持递归创建子权限。
    """
    try:
        service = PermissionService(context)
        new_perm = await service.create_permission(perm_in)
        return JsonResponse(data=new_perm)
    except (ServiceException, NotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/{permission_name:path}", response_model=JsonResponse[PermissionReadNode], summary="[Admin] Update Permission")
async def update_permission(
    perm_in: PermissionUpdate,
    permission_name: str = Path(..., description="The unique name of the permission to update, e.g., 'project:write'"),
    context: AppContext = AuthContextDep
):
    """
    [Admin] 更新一个已存在的权限定义。
    注意: 权限的 'name' 是不可变的。
    """
    try:
        service = PermissionService(context)
        updated_perm = await service.update_permission(permission_name, perm_in)
        return JsonResponse(data=updated_perm)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ServiceException as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/{permission_name:path}", response_model=MsgResponse, summary="[Admin] Delete Permission")
async def delete_permission(
    permission_name: str = Path(..., description="The unique name of the permission to delete."),
    context: AppContext = AuthContextDep
):
    """
    [Admin] 删除一个权限及其所有子权限。这是一个高危操作。
    """
    try:
        service = PermissionService(context)
        await service.delete_permission(permission_name)
        return MsgResponse(msg="Permission and its children deleted successfully.")
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ServiceException as e:
        raise HTTPException(status_code=400, detail=str(e))