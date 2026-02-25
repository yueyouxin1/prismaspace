# src/app/api/v1/module_type.py

from fastapi import APIRouter, status, HTTPException
from typing import List
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep, PublicContextDep
from app.schemas.common import JsonResponse, MsgResponse
from app.schemas.module.service_module_type_schemas import ServiceModuleTypeCreate, ServiceModuleTypeUpdate, ServiceModuleTypeRead
from app.services.module.service_module_type_service import ServiceModuleTypeService
from app.services.exceptions import ServiceException, NotFoundError

router = APIRouter()

@router.post("", response_model=JsonResponse[ServiceModuleTypeRead], status_code=status.HTTP_201_CREATED, summary="[Admin] Create Service Module Type")
async def create_type(type_in: ServiceModuleTypeCreate, context: AppContext = AuthContextDep):
    try:
        service = ServiceModuleTypeService(context)
        new_type = await service.create_type(type_in)
        return JsonResponse(data=new_type)
    except (ServiceException, NotFoundError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.get("", response_model=JsonResponse[List[ServiceModuleTypeRead]], summary="List All Service Module Types")
async def list_types(context: AppContext = AuthContextDep): # Requires login to view
    service = ServiceModuleTypeService(context)
    types = await service.list_types()
    return JsonResponse(data=types)

@router.put("/{name}", response_model=JsonResponse[ServiceModuleTypeRead], summary="[Admin] Update Service Module Type")
async def update_type(name: str, type_in: ServiceModuleTypeUpdate, context: AppContext = AuthContextDep):
    try:
        service = ServiceModuleTypeService(context)
        updated_type = await service.update_type(name, type_in)
        return JsonResponse(data=updated_type)
    except (ServiceException, NotFoundError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.delete("/{name}", response_model=MsgResponse, summary="[Admin] Delete Service Module Type")
async def delete_type(name: str, context: AppContext = AuthContextDep):
    try:
        service = ServiceModuleTypeService(context)
        await service.delete_type(name)
        return MsgResponse(msg=f"Service module type '{name}' deleted successfully.")
    except (ServiceException, NotFoundError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))