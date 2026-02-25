# app/api/v1/resource.py

from fastapi import APIRouter, Depends, Body, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Any, Dict, List
from pydantic import ValidationError
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep
from app.schemas.common import JsonResponse, MsgResponse
from app.schemas.resource.resource_schemas import (
    ResourceRead,
    ResourceDetailRead,
    ResourceCreate,
    ResourceUpdate,
    InstancePublish,
    ResourceDependencyRead,
)
from app.schemas.project.project_resource_schemas import (
    ProjectResourceReferenceCreate,
    ProjectResourceReferenceRead,
)
from app.schemas.resource.resource_ref_schemas import ReferenceCreate, ReferenceRead, BatchSyncReferences
from app.services.resource.resource_service import ResourceService
from app.services.project.project_resource_ref_service import ProjectResourceRefService
from app.services.resource.resource_ref_service import ResourceRefService
from app.services.exceptions import PermissionDeniedError, NotFoundError, ServiceException

# 同样，使用混合路由模式
workspace_router = APIRouter() # /workspaces/{uuid}/resources
project_router = APIRouter() # /projects/{uuid}/resources (references)
resource_router = APIRouter() # /resources/{uuid}
instance_router = APIRouter() # /instances/{uuid}

@workspace_router.post("", response_model=JsonResponse[ResourceRead], status_code=status.HTTP_201_CREATED, summary="Create a Resource in a Workspace")
async def create_resource(
    workspace_uuid: str,
    resource_in: ResourceCreate,
    context: AppContext = AuthContextDep
):
    try:
        service = ResourceService(context)
        new_resource = await service.create_resource_in_workspace(workspace_uuid, resource_in, context.actor)
        return JsonResponse(data=new_resource)
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ServiceException as e:
        raise HTTPException(status_code=400, detail=str(e))

@resource_router.put(
    "/{resource_uuid}",
    response_model=JsonResponse[ResourceRead],
    summary="Update Resource Metadata"
)
async def update_resource(
    resource_uuid: str,
    resource_in: ResourceUpdate,
    context: AppContext = AuthContextDep
):
    try:
        service = ResourceService(context)
        updated_resource = await service.update_resource_metadata(resource_uuid, resource_in, context.actor)
        # ResourceRead schema 已经存在，可以直接复用
        return JsonResponse(data=updated_resource)
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ServiceException as e:
        raise HTTPException(status_code=400, detail=str(e))

@resource_router.delete(
    "/{resource_uuid}",
    response_model=MsgResponse,
    summary="Delete a Resource and all its versions"
)
async def delete_resource(
    resource_uuid: str,
    context: AppContext = AuthContextDep
):
    try:
        service = ResourceService(context)
        await service.delete_resource(resource_uuid, context.actor)
        return MsgResponse(msg="Resource deleted successfully.")
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

@workspace_router.get("", response_model=JsonResponse[List[ResourceRead]], summary="List Resources in a Workspace")
async def list_resources(
    workspace_uuid: str,
    context: AppContext = AuthContextDep
):
    try:
        service = ResourceService(context)
        resources = await service.get_resources_in_workspace(workspace_uuid, context.actor)
        return JsonResponse(data=resources)
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ServiceException as e:
        raise HTTPException(status_code=400, detail=str(e))

@project_router.post(
    "",
    response_model=JsonResponse[ProjectResourceReferenceRead],
    status_code=status.HTTP_201_CREATED,
    summary="Add a Resource Reference to a Project"
)
async def add_project_resource_reference(
    project_uuid: str,
    ref_in: ProjectResourceReferenceCreate,
    context: AppContext = AuthContextDep
):
    try:
        service = ProjectResourceRefService(context)
        new_ref = await service.add_reference(project_uuid, ref_in, context.actor)
        return JsonResponse(data=new_ref)
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ServiceException as e:
        raise HTTPException(status_code=400, detail=str(e))

@project_router.get(
    "",
    response_model=JsonResponse[List[ProjectResourceReferenceRead]],
    summary="List Project Resource References"
)
async def list_project_resource_references(
    project_uuid: str,
    context: AppContext = AuthContextDep
):
    try:
        service = ProjectResourceRefService(context)
        refs = await service.list_references(project_uuid, context.actor)
        return JsonResponse(data=refs)
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ServiceException as e:
        raise HTTPException(status_code=400, detail=str(e))

@project_router.delete(
    "/{resource_uuid}",
    response_model=MsgResponse,
    summary="Remove a Resource Reference from a Project"
)
async def remove_project_resource_reference(
    project_uuid: str,
    resource_uuid: str,
    context: AppContext = AuthContextDep
):
    try:
        service = ProjectResourceRefService(context)
        await service.remove_reference(project_uuid, resource_uuid, context.actor)
        return MsgResponse(msg="Project resource reference removed successfully.")
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

@resource_router.get(
    "/{resource_uuid}",
    response_model=JsonResponse[ResourceDetailRead],
    summary="Get aggregated details of a single Resource"
)
async def get_resource_details(
    resource_uuid: str,
    context: AppContext = AuthContextDep
):
    try:
        service = ResourceService(context)
        details = await service.get_resource_details_by_uuid(resource_uuid, context.actor)
        return JsonResponse(data=details)
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

@resource_router.get(
    "/{resource_uuid}/instances",
    response_model=JsonResponse[List[Dict[str, Any]]],
    summary="List all instances of a Resource"
)
async def list_resource_instances(
    resource_uuid: str,
    context: AppContext = AuthContextDep
):
    try:
        service = ResourceService(context)
        instances = await service.get_resource_instances_by_uuid(resource_uuid, context.actor)
        return JsonResponse(data=instances)
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ServiceException as e:
        raise HTTPException(status_code=400, detail=str(e))

@instance_router.get(
    "/{instance_uuid}", 
    response_model=JsonResponse[Dict[str, Any]], # [新] 响应是通用的字典
    summary="Get Any Resource Instance Details"
)
async def get_instance( # [新] 重命名为通用名称
    instance_uuid: str,
    context: AppContext = AuthContextDep
):
    try:
        service = ResourceService(context)
        # 1. 调用通用的 getter
        response_data = await service.get_instance_by_uuid(instance_uuid, context.actor)
        return JsonResponse(data=response_data)
        
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ServiceException as e:
        raise HTTPException(status_code=400, detail=str(e))

@instance_router.put(
    "/{instance_uuid}", 
    # [新] 响应模型现在是通用的字典，因为返回类型是动态的
    response_model=JsonResponse[Dict[str, Any]], 
    summary="Update Any Resource Instance"
)
async def update_instance(
    instance_uuid: str,
    # [新] 直接接收一个原始的字典/JSON体
    update_data: Dict[str, Any] = Body(...),
    context: AppContext = AuthContextDep
):
    try:
        service = ResourceService(context)
        response_data = await service.update_instance_by_uuid(
            instance_uuid, update_data, context.actor
        )
        return JsonResponse(data=response_data)
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except (NotFoundError, ServiceException, ValidationError) as e: # [新] 捕获Pydantic验证错误
        # 将Pydantic的ValidationError转换为HTTP 422
        if isinstance(e, ValidationError):
            raise HTTPException(status_code=422, detail=e.errors())
        raise HTTPException(status_code=400, detail=str(e))

@instance_router.delete(
    "/{instance_uuid}",
    response_model=MsgResponse,
    summary="Delete a Resource Instance version"
)
async def delete_instance(
    instance_uuid: str,
    context: AppContext = AuthContextDep
):
    try:
        service = ResourceService(context)
        await service.delete_instance_by_uuid(instance_uuid, context.actor)
        return MsgResponse(msg="Resource Instance deleted successfully.")
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

@instance_router.post(
    "/{instance_uuid}/publish",
    response_model=JsonResponse[Dict[str, Any]],
    status_code=status.HTTP_201_CREATED,
    summary="Publish a Resource Instance"
)
async def publish_instance(
    instance_uuid: str,
    publish_data: InstancePublish,
    context: AppContext = AuthContextDep
):
    """
    Creates a new, immutable, published snapshot from a workspace instance.
    Returns the newly created published instance.
    """
    try:
        service = ResourceService(context)
        # 1. 调用服务层核心方法
        response_data = await service.publish_instance(
            instance_uuid, publish_data, context.actor
        )
        return JsonResponse(data=response_data, status_code=status.HTTP_201_CREATED)
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except (NotFoundError, ServiceException, ValidationError) as e:
        if isinstance(e, ValidationError):
            raise HTTPException(status_code=422, detail=e.errors())
        raise HTTPException(status_code=400, detail=str(e))

@instance_router.post(
    "/{instance_uuid}/archive",
    response_model=JsonResponse[Dict[str, Any]], # 返回更新后的实例信息
    summary="Archive a Published Resource Instance"
)
async def archive_instance(
    instance_uuid: str,
    context: AppContext = AuthContextDep
):
    """
    Manually archives a PUBLISHED resource instance, making it unavailable for execution.
    This action does not affect any other versions.
    """
    try:
        service = ResourceService(context)
        response_data = await service.archive_instance(instance_uuid, context.actor)
        return JsonResponse(data=response_data)
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except (NotFoundError, ServiceException) as e:
        # ServiceException 可能包含 "Only a published instance can be archived."
        raise HTTPException(status_code=400, detail=str(e))

@instance_router.get(
    "/{instance_uuid}/refs",
    response_model=JsonResponse[List[ReferenceRead]],
    summary="List Resource Dependencies"
)
async def list_resource_references(
    instance_uuid: str,
    context: AppContext = AuthContextDep
):
    service = ResourceRefService(context)
    refs = await service.list_dependencies(instance_uuid, context.actor)
    return JsonResponse(data=refs)

@instance_router.post(
    "/{instance_uuid}/refs",
    response_model=JsonResponse[ReferenceRead],
    summary="Add Dependency Reference"
)
async def add_resource_reference(
    instance_uuid: str,
    ref_data: ReferenceCreate,
    context: AppContext = AuthContextDep
):
    service = ResourceRefService(context)
    new_ref = await service.add_dependency(instance_uuid, ref_data, context.actor)
    return JsonResponse(data=new_ref)

@instance_router.delete(
    "/{instance_uuid}/refs/{target_instance_uuid}",
    response_model=MsgResponse,
    summary="Remove Dependency Reference"
)
async def remove_resource_reference(
    instance_uuid: str,
    target_instance_uuid: str,
    source_node_uuid: str = None, # Optional Query Param to be specific
    context: AppContext = AuthContextDep
):
    service = ResourceRefService(context)
    await service.remove_dependency(instance_uuid, target_instance_uuid, source_node_uuid, context.actor)
    return MsgResponse(msg="Reference removed.")

@instance_router.get(
    "/{instance_uuid}/dependencies",
    response_model=JsonResponse[List[ResourceDependencyRead]],
    summary="List Resource Dependencies (Resolved)"
)
async def list_instance_dependencies(
    instance_uuid: str,
    context: AppContext = AuthContextDep
):
    try:
        service = ResourceService(context)
        deps = await service.get_instance_dependencies(instance_uuid, context.actor)
        return JsonResponse(data=deps)
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
