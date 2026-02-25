# app/api/v1/project.py

from fastapi import APIRouter, Query, status, HTTPException
from typing import List, Optional, Literal
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep
from app.schemas.common import JsonResponse, MsgResponse
from app.schemas.project.project_schemas import ProjectRead, ProjectCreate, ProjectUpdate
from app.schemas.project.project_env_schemas import ProjectEnvConfigRead, ProjectEnvConfigUpdate
from app.schemas.project.project_dependency_schemas import ProjectDependencyGraphRead
from app.services.project.project_service import ProjectService
from app.services.project.project_dependency_service import ProjectDependencyService
from app.services.exceptions import PermissionDeniedError, NotFoundError

# 创建两个独立的router，以实现混合路由模式
router = APIRouter() # 用于顶级路由 /projects
workspace_router = APIRouter() # 用于嵌套路由 /workspaces/{uuid}/projects

@workspace_router.post("", response_model=JsonResponse[ProjectRead], status_code=status.HTTP_201_CREATED, summary="Create Project in Workspace")
async def create_project(
    workspace_uuid: str,
    project_in: ProjectCreate,
    context: AppContext = AuthContextDep
):
    try:
        service = ProjectService(context)
        new_project_obj = await service.create_project_in_workspace(workspace_uuid, project_in, context.actor)
        return JsonResponse(data=new_project_obj)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))

@workspace_router.get("", response_model=JsonResponse[List[ProjectRead]], summary="List Projects in Workspace")
async def list_projects(
    workspace_uuid: str,
    main_application_type: Optional[Literal["uiapp", "agent", "unset"]] = Query(
        default=None,
        description="按主应用类型筛选：uiapp/agent/unset"
    ),
    context: AppContext = AuthContextDep
):
    try:
        service = ProjectService(context)
        projects = await service.get_projects_in_workspace(
            workspace_uuid,
            context.actor,
            main_application_type=main_application_type
        )
        return JsonResponse(data=projects)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))

@router.get("/{project_uuid}", response_model=JsonResponse[ProjectRead], summary="Get Project Details")
async def get_project(
    project_uuid: str,
    context: AppContext = AuthContextDep
):
    try:
        service = ProjectService(context)
        project = await service.get_project_by_uuid(project_uuid, context.actor)
        return JsonResponse(data=project)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))

@router.put("/{project_uuid}", response_model=JsonResponse[ProjectRead], summary="Update Project")
async def update_project(
    project_uuid: str,
    project_in: ProjectUpdate,
    context: AppContext = AuthContextDep
):
    try:
        service = ProjectService(context)
        updated_project = await service.update_project_by_uuid(project_uuid, project_in, context.actor)
        return JsonResponse(data=updated_project)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))

@router.delete("/{project_uuid}", response_model=MsgResponse, summary="Delete Project")
async def delete_project(
    project_uuid: str,
    context: AppContext = AuthContextDep
):
    try:
        service = ProjectService(context)
        await service.delete_project_by_uuid(project_uuid, context.actor)
        return MsgResponse(msg="Project deleted successfully.")
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))

@router.get(
    "/{project_uuid}/env-config",
    response_model=JsonResponse[ProjectEnvConfigRead],
    summary="Get Project Environment Config"
)
async def get_project_env_config(
    project_uuid: str,
    context: AppContext = AuthContextDep
):
    try:
        service = ProjectService(context)
        config = await service.get_project_env_config(project_uuid, context.actor)
        return JsonResponse(data=config)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))

@router.put(
    "/{project_uuid}/env-config",
    response_model=JsonResponse[ProjectEnvConfigRead],
    summary="Update Project Environment Config"
)
async def update_project_env_config(
    project_uuid: str,
    env_config_in: ProjectEnvConfigUpdate,
    context: AppContext = AuthContextDep
):
    try:
        service = ProjectService(context)
        config = await service.update_project_env_config(project_uuid, env_config_in, context.actor)
        return JsonResponse(data=config)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))

@router.delete(
    "/{project_uuid}/env-config",
    response_model=MsgResponse,
    summary="Clear Project Environment Config"
)
async def clear_project_env_config(
    project_uuid: str,
    context: AppContext = AuthContextDep
):
    try:
        service = ProjectService(context)
        await service.clear_project_env_config(project_uuid, context.actor)
        return MsgResponse(msg="Project environment config cleared.")
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))

@router.get(
    "/{project_uuid}/dependency-graph",
    response_model=JsonResponse[ProjectDependencyGraphRead],
    summary="Get Project Dependency Graph"
)
async def get_project_dependency_graph(
    project_uuid: str,
    context: AppContext = AuthContextDep
):
    try:
        service = ProjectDependencyService(context)
        graph = await service.get_dependency_graph(project_uuid, context.actor)
        return JsonResponse(data=graph)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
