# src/app/services/module/service_module_credential_service.py

from typing import List
from app.core.context import AppContext
from app.core.encryption import encrypt
from app.models import User, Workspace, ServiceModuleCredential
from app.dao.workspace.workspace_dao import WorkspaceDao
from app.dao.module.service_module_credential_dao import ServiceModuleCredentialDao
from app.schemas.module.service_module_credential_schemas import (
    ServiceModuleCredentialCreate, 
    ServiceModuleCredentialUpdate, 
    ServiceModuleCredentialRead
)
from app.services.base_service import BaseService
from app.services.exceptions import NotFoundError, PermissionDeniedError, ServiceException

class ServiceModuleCredentialService(BaseService):
    """
    [V4.5 FINAL] Manages the CRUD lifecycle for ServiceModuleCredentials within a Workspace context.
    """
    def __init__(self, context: AppContext):
        self.context = context
        self.db = context.db
        self.dao = ServiceModuleCredentialDao(context.db)
        self.workspace_dao = WorkspaceDao(context.db)

    async def _load_and_authorize_workspace(self, workspace_uuid: str, permissions: List[str]) -> Workspace:
        """[SECURITY CORE] Centralized helper to load and authorize access to a workspace."""
        workspace = await self.workspace_dao.get_by_uuid(workspace_uuid)
        if not workspace:
            raise NotFoundError("Workspace not found.")
        
        # The V5.0 PermissionEvaluator will handle inheritance check (e.g., workspace:read) automatically.
        await self.context.perm_evaluator.ensure_can(permissions, target=workspace)
        
        return workspace

    async def get_credentials_for_workspace(self, workspace_uuid: str, actor: User) -> List[ServiceModuleCredentialRead]:
        workspace = await self._load_and_authorize_workspace(workspace_uuid, ["workspace:credential:servicemodule:read"])
        
        credentials = await self.dao.get_for_workspace(workspace.id)
        return [ServiceModuleCredentialRead.model_validate(c) for c in credentials]

    async def create_credential(self, workspace_uuid: str, data: ServiceModuleCredentialCreate, actor: User) -> ServiceModuleCredentialRead:
        workspace = await self._load_and_authorize_workspace(workspace_uuid, ["workspace:credential:servicemodule:create"])
        
        # Check for existing credential for this provider in the workspace
        existing = await self.dao.get_by_workspace_and_provider(data.provider_id, workspace.id)
        if existing:
            raise ServiceException(f"A credential for provider '{existing.provider_id}' already exists in this workspace. Please update it instead.")
            
        new_cred = await self.dao.add(ServiceModuleCredential(
            provider_id=data.provider_id,
            label=data.label,
            encrypted_value=encrypt(data.value),
            encrypted_endpoint=encrypt(str(data.endpoint)) if data.endpoint else None,
            region=data.region,
            attributes=data.attributes,
            workspace_id=workspace.id
        ))
        return ServiceModuleCredentialRead.model_validate(new_cred)

    async def update_credential(self, workspace_uuid: str, cred_uuid: str, data: ServiceModuleCredentialUpdate, actor: User) -> ServiceModuleCredentialRead:
        workspace = await self._load_and_authorize_workspace(workspace_uuid, ["workspace:credential:servicemodule:update"])
        
        cred_to_update = await self.dao.get_one(where={"uuid": cred_uuid, "workspace_id": workspace.id})
        if not cred_to_update:
            raise NotFoundError("Credential not found in this workspace.")
        
        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            # No fields to update
            return ServiceModuleCredentialRead.model_validate(cred_to_update)

        if "label" in update_data:
            cred_to_update.label = update_data["label"]
        if "value" in update_data:
            cred_to_update.encrypted_value = encrypt(update_data["value"])
        if "endpoint" in update_data:
            cred_to_update.encrypted_endpoint = encrypt(str(update_data["endpoint"])) if update_data["endpoint"] else None
        if "region" in update_data:
            cred_to_update.region = update_data["region"]
        if "attributes" in update_data:
            cred_to_update.attributes = update_data["attributes"]
            
        await self.db.flush()
        await self.db.refresh(cred_to_update)
        return ServiceModuleCredentialRead.model_validate(cred_to_update)

    async def delete_credential(self, workspace_uuid: str, cred_uuid: str, actor: User) -> None:
        workspace = await self._load_and_authorize_workspace(workspace_uuid, ["workspace:credential:servicemodule:delete"])
        
        cred_to_delete = await self.dao.get_one(where={"uuid": cred_uuid, "workspace_id": workspace.id})
        if not cred_to_delete:
            raise NotFoundError("Credential not found in this workspace.")

        await self.db.delete(cred_to_delete)
        await self.db.flush()