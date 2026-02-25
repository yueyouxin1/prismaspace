# src/app/api/v1/module_provider.py

from fastapi import APIRouter, status, HTTPException
from typing import List
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep, PublicContextDep
from app.schemas.common import JsonResponse, MsgResponse
from app.schemas.module.service_module_provider_schemas import ServiceModuleProviderCreate, ServiceModuleProviderUpdate, ServiceModuleProviderRead
from app.services.module.service_module_provider_service import ServiceModuleProviderService
from app.services.exceptions import ServiceException, NotFoundError

router = APIRouter()

@router.post("", response_model=JsonResponse[ServiceModuleProviderRead], status_code=status.HTTP_201_CREATED, summary="[Admin] Create Service Module Provider")
async def create_provider(provider_in: ServiceModuleProviderCreate, context: AppContext = AuthContextDep):
    try:
        service = ServiceModuleProviderService(context)
        new_provider = await service.create_provider(provider_in)
        return JsonResponse(data=new_provider)
    except (ServiceException, NotFoundError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.get("", response_model=JsonResponse[List[ServiceModuleProviderRead]], summary="List All Service Module Providers")
async def list_providers(context: AppContext = AuthContextDep): # Requires login to view
    service = ServiceModuleProviderService(context)
    providers = await service.list_providers()
    return JsonResponse(data=providers)

@router.put("/{name}", response_model=JsonResponse[ServiceModuleProviderRead], summary="[Admin] Update Service Module Provider")
async def update_provider(name: str, provider_in: ServiceModuleProviderUpdate, context: AppContext = AuthContextDep):
    try:
        service = ServiceModuleProviderService(context)
        updated_provider = await service.update_provider(name, provider_in)
        return JsonResponse(data=updated_provider)
    except (ServiceException, NotFoundError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.delete("/{name}", response_model=MsgResponse, summary="[Admin] Delete Service Module Provider")
async def delete_provider(name: str, context: AppContext = AuthContextDep):
    try:
        service = ServiceModuleProviderService(context)
        await service.delete_provider(name)
        return MsgResponse(msg=f"Service module provider '{name}' deleted successfully.")
    except (ServiceException, NotFoundError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))