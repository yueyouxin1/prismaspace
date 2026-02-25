# src/app/api/v1/module.py

from fastapi import APIRouter, Query, HTTPException, status
from typing import List, Optional
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep, PublicContextDep
from app.schemas.common import JsonResponse
from app.schemas.module.service_module_schemas import ServiceModuleCreateFull, ServiceModuleRead
from app.services.module.service_module_service import ServiceModuleService
from app.services.exceptions import NotFoundError, ServiceException, ConfigurationError

router = APIRouter()

@router.post(
    "", 
    response_model=JsonResponse[ServiceModuleRead], 
    status_code=status.HTTP_201_CREATED, 
    summary="[Admin] Create a new Service Module"
)
async def create_service_module(
    data: ServiceModuleCreateFull, 
    context: AppContext = AuthContextDep
):
    """
    [Admin] Registers a new service module and its initial version in the system.
    This automatically creates the corresponding usage permission.
    """
    try:
        service = ServiceModuleService(context)
        new_module = await service.create_module(
            module_data=data.module,
            version_data=data.version
        )
        return JsonResponse(data=new_module)
    except (ServiceException, ConfigurationError, NotFoundError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.get(
    "/me/available",
    response_model=JsonResponse[List[ServiceModuleRead]],
    summary="List Actor's Available Service Modules by Type and Context"
)
async def list_my_available_modules(
    workspace_uuid: str,
    module_type: str = Query(..., alias="type"),
    context: AppContext = AuthContextDep
):
    try:
        service = ServiceModuleService(context)
        modules = await service.list_available_modules_for_actor(
            context.actor, 
            module_type,
            workspace_uuid
        )
        return JsonResponse(data=modules)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
