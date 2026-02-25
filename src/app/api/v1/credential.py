# src/app/api/v1/credential.py

from fastapi import APIRouter, status, HTTPException
from typing import List
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep
from app.schemas.common import JsonResponse, MsgResponse
from app.schemas.module.service_module_credential_schemas import *
from app.services.module.service_module_credential_service import ServiceModuleCredentialService
from app.services.exceptions import NotFoundError, PermissionDeniedError, ServiceException

# Router for workspace-scoped credential actions
router = APIRouter()

# Router for top-level, static information
supported_providers_router = APIRouter()

SUPPORTED_PROVIDERS = [{"name": "openai", "label": "OpenAI"}, {"name": "anthropic", "label": "Anthropic"}, {"name": "aliyun", "label": "Aliyun"}]

@supported_providers_router.get("/supported-providers", response_model=JsonResponse[List[ProviderInfo]])
async def list_supported_providers():
    """Lists all service providers that support user-provided credentials (BYOK)."""
    return JsonResponse(data=SUPPORTED_PROVIDERS)

@router.get("", response_model=JsonResponse[List[ServiceModuleCredentialRead]], summary="List Credentials in Workspace")
async def list_credentials(workspace_uuid: str, context: AppContext = AuthContextDep):
    service = ServiceModuleCredentialService(context)
    creds = await service.get_credentials_for_workspace(workspace_uuid, context.actor)
    return JsonResponse(data=creds)

@router.post("", response_model=JsonResponse[ServiceModuleCredentialRead], status_code=status.HTTP_201_CREATED, summary="Create Credential in Workspace")
async def create_credential(workspace_uuid: str, cred_in: ServiceModuleCredentialCreate, context: AppContext = AuthContextDep):
    service = ServiceModuleCredentialService(context)
    new_cred = await service.create_credential(workspace_uuid, cred_in, context.actor)
    return JsonResponse(data=new_cred)

@router.put("/{cred_uuid}", response_model=JsonResponse[ServiceModuleCredentialRead], summary="Update Credential in Workspace")
async def update_credential(workspace_uuid: str, cred_uuid: str, cred_in: ServiceModuleCredentialUpdate, context: AppContext = AuthContextDep):
    service = ServiceModuleCredentialService(context)
    updated_cred = await service.update_credential(workspace_uuid, cred_uuid, cred_in, context.actor)
    return JsonResponse(data=updated_cred)

@router.delete("/{cred_uuid}", response_model=MsgResponse, summary="Delete Credential from Workspace", status_code=status.HTTP_200_OK)
async def delete_credential(workspace_uuid: str, cred_uuid: str, context: AppContext = AuthContextDep):
    service = ServiceModuleCredentialService(context)
    await service.delete_credential(workspace_uuid, cred_uuid, context.actor)
    return MsgResponse(msg="Credential deleted successfully.")