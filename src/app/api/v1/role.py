# src/app/api/v1/role.py

from fastapi import APIRouter, status, HTTPException
from typing import List
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep
from app.schemas.common import JsonResponse, MsgResponse
from app.schemas.permission.role_schemas import RoleCreate, RoleUpdate, RoleRead
from app.schemas.permission.permission_schemas import PermissionReadNode 
from app.services.permission.role_service import RoleService
from app.services.permission.permission_service import PermissionService
from app.services.exceptions import ServiceException, NotFoundError

# 这个 router 将被嵌套在 /teams/{team_uuid}/ 下
router = APIRouter()

@router.get("/assignable-permissions", response_model=JsonResponse[List[PermissionReadNode]], summary="List Assignable Permissions for Team Roles")
async def list_assignable_permissions(team_uuid: str, context: AppContext = AuthContextDep):
    """
    获取可分配给此团队角色的所有权限的树状结构。
    """
    try:
        # 注意: 这里我们直接使用 PermissionService
        service = PermissionService(context)
        # 假设 PermissionService 中有 get_assignable_permission_tree_for_team 方法
        permissions = await service.get_assignable_permission_tree_for_team(team_uuid)
        return JsonResponse(data=permissions)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("", response_model=JsonResponse[List[RoleRead]], summary="List Team Roles")
async def list_team_roles(team_uuid: str, context: AppContext = AuthContextDep):
    try:
        service = RoleService(context)
        roles = await service.get_team_roles(team_uuid)
        return JsonResponse(data=roles)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("", response_model=JsonResponse[RoleRead], status_code=status.HTTP_201_CREATED, summary="Create Team Role")
async def create_team_role(team_uuid: str, role_in: RoleCreate, context: AppContext = AuthContextDep):
    try:
        service = RoleService(context)
        new_role = await service.create_team_role(team_uuid, role_in)
        return JsonResponse(data=new_role)
    except (ServiceException, NotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/{role_uuid}", response_model=JsonResponse[RoleRead], summary="Update Team Role")
async def update_team_role(team_uuid: str, role_uuid: str, role_in: RoleUpdate, context: AppContext = AuthContextDep):
    try:
        service = RoleService(context)
        updated_role = await service.update_team_role(team_uuid, role_uuid, role_in)
        return JsonResponse(data=updated_role)
    except (ServiceException, NotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/{role_uuid}", response_model=MsgResponse, summary="Delete Team Role")
async def delete_team_role(team_uuid: str, role_uuid: str, context: AppContext = AuthContextDep):
    try:
        service = RoleService(context)
        await service.delete_team_role(team_uuid, role_uuid)
        return MsgResponse(msg="Role deleted successfully.")
    except (ServiceException, NotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))