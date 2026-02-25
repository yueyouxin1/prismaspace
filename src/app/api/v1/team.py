# app/api/v1/team.py

from fastapi import APIRouter, Depends, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep
from app.schemas.common import JsonResponse, MsgResponse
from app.schemas.identity.team_schemas import TeamRead, TeamCreate, TeamUpdate, TeamMemberRead
from app.services.identity.team_service import TeamService
from app.services.exceptions import ServiceException, PermissionDeniedError, NotFoundError, ConfigurationError

router = APIRouter()

@router.get("", response_model=JsonResponse[List[TeamRead]], summary="List User's Teams")
async def list_user_teams(
    context: AppContext = AuthContextDep
):
    service = TeamService(context)
    teams = await service.get_teams_for_user(actor=context.actor)
    return JsonResponse(data=teams)

@router.post("", response_model=JsonResponse[TeamRead], status_code=status.HTTP_201_CREATED, summary="Create a new Team")
async def create_team(
    team_in: TeamCreate,
    context: AppContext = AuthContextDep
):
    try:
        service = TeamService(context)
        new_team = await service.create_team(team_data=team_in, actor=context.actor)
        return JsonResponse(data=new_team)
    except ConfigurationError as e:
        raise HTTPException(status_code=500, detail=str(e)) # Server config error
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))

@router.get("/{team_uuid}", response_model=JsonResponse[TeamRead], summary="Get Team Details")
async def get_team(
    team_uuid: str,
    context: AppContext = AuthContextDep
):
    try:
        service = TeamService(context)
        team = await service.get_team_by_uuid(team_uuid, context.actor)
        return JsonResponse(data=team)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))

@router.put("/{team_uuid}", response_model=JsonResponse[TeamRead], summary="Update Team")
async def update_team(
    team_uuid: str,
    team_in: TeamUpdate,
    context: AppContext = AuthContextDep
):
    try:
        service = TeamService(context)
        updated_team = await service.update_team_by_uuid(team_uuid, team_in, context.actor)
        return JsonResponse(data=updated_team)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))

@router.delete("/{team_uuid}", response_model=MsgResponse, summary="Delete Team")
async def delete_team(
    team_uuid: str,
    context: AppContext = AuthContextDep
):
    try:
        service = TeamService(context)
        await service.delete_team_by_uuid(team_uuid, context.actor)
        return MsgResponse(msg="Team deleted successfully.")
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))

@router.get(
    "/{team_uuid}/members",
    response_model=JsonResponse[List[TeamMemberRead]],
    summary="List Team Members"
)
async def list_team_members(
    team_uuid: str,
    context: AppContext = AuthContextDep
):
    """获取指定团队的成员列表。"""
    try:
        service = TeamService(context)
        members = await service.get_team_members(team_uuid, context.actor)
        return JsonResponse(data=members)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.delete(
    "/{team_uuid}/members/{member_uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a Member from Team"
)
async def remove_team_member(
    team_uuid: str,
    member_uuid: str,
    context: AppContext = AuthContextDep
):
    """从团队中移除一个成员。"""
    try:
        service = TeamService(context)
        await service.remove_team_member(team_uuid, member_uuid, context.actor)
    except (NotFoundError, ServiceException) as e:
        # 将业务异常和未找到都视为客户端错误
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    
    # 成功时返回 204 No Content，不需要响应体
    return