# src/app/services/module/service_module_service.py

from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload, contains_eager 

from app.core.context import AppContext
from app.models import User, Product, Workspace
from app.models.module import ServiceModule, ServiceModuleType, ServiceModuleVersion, ServiceModuleStatus
from app.schemas.module.service_module_schemas import ServiceModuleRead, ServiceModuleCreate, ServiceModuleVersionCreate, ServiceModuleVersionRead
from app.services.base_service import BaseService
from app.dao.product.product_dao import ProductDao
from app.dao.workspace.workspace_dao import WorkspaceDao 
from app.dao.module.service_module_dao import ServiceModuleDao, ServiceModuleVersionDao
from app.services.exceptions import NotFoundError
from app.system.module.service_module_manager import ServiceModuleManager
from app.system.vectordb.manager import SystemVectorManager
from .service_module_credential_provider import ServiceModuleCredentialProvider
from .types.service_module import ModuleRuntimeContext

class ServiceModuleService(BaseService):
    """
    [V4.4 FINAL] A core service responsible for the three missions of service modules:
    1. Public Discovery (Catalog)
    2. Contextual Availability (for UIs)
    3. Secure Runtime Context Building (for Engines)
    """
    def __init__(self, context: AppContext):
        self.context = context
        self.db = context.db
        # Internal dependencies
        self.dao = ServiceModuleDao(self.db)
        self.product_dao = ProductDao(self.db)
        self.workspace_dao = WorkspaceDao(self.db)
        self.smv_dao = ServiceModuleVersionDao(self.db)
        self.credential_provider = ServiceModuleCredentialProvider(self.db)
        self.manager = ServiceModuleManager(self.db)
        self.vector_ops = SystemVectorManager(self.db, context.vector_manager)

    # ===================================================================
    # MANAGEMENT LOGIC
    # ===================================================================
    async def create_module(self, module_data: ServiceModuleCreate, version_data: ServiceModuleVersionCreate) -> ServiceModuleRead:
        await self.context.perm_evaluator.ensure_can(["platform:servicemodule:manage"])
        
        new_module = await self.manager.create_module_with_versions(
            ServiceModuleCreateFull(module=module_data, versions=[version_data])
        )
        
        # [Hook] Create Collection if Embedding
        if new_module.type.name == 'embedding':
            for version in new_module.versions:
                await self.vector_ops.ensure_collection_for_version(version)
        
        return ServiceModuleRead.model_validate(new_module)

    async def create_version_for_module(self, module_id: int, version_data: ServiceModuleVersionCreate) -> ServiceModuleVersionRead:
        """[New] Add a version to an existing module."""
        await self.context.perm_evaluator.ensure_can(["platform:servicemodule:manage"])
        
        module = await self.dao.get_by_pk(module_id, withs=["type", "provider"])
        if not module: raise NotFoundError("Module not found.")

        new_version = await self.manager.create_version(module, version_data)
        
        # [Hook] Create Collection
        if module.type.name == 'embedding':
            await self.vector_ops.ensure_collection_for_version(new_version)
            
        return ServiceModuleVersionRead.model_validate(new_version)

    async def delete_module(self, module_id: int) -> None:
        await self.context.perm_evaluator.ensure_can(["platform:servicemodule:manage"])
        
        # 1. Load module to identify versions for cleanup
        module = await self.dao.get_one(where={"id": module_id}, withs=["versions", "type", "provider"])
        if not module: raise NotFoundError("Module not found.")

        # 2. [Hook] Drop Collections (Pre-delete)
        if module.type.name == 'embedding':
            for version in module.versions:
                await self.vector_ops.drop_collection_for_version(version)

        # 3. DB Delete
        await self.manager.delete_module(module_id)

    async def delete_version(self, version_id: int) -> None:
        await self.context.perm_evaluator.ensure_can(["platform:servicemodule:manage"])
        
        # 1. Load version
        version = await self.smv_dao.get_one(
            where={"id": version_id}, 
            withs=["service_module.type", "service_module.provider"]
        )
        if not version: raise NotFoundError("Version not found.")

        # 2. [Hook] Drop Collection
        if version.service_module.type.name == 'embedding':
            await self.vector_ops.drop_collection_for_version(version)

        # 3. DB Delete
        await self.manager.delete_version(version_id)
        
    # ===================================================================
    # DISCOVERY LOGIC (Unchanged but confirmed correct)
    # ===================================================================
    async def list_available_modules_for_actor(
        self, 
        actor: User, 
        module_type: str,
        workspace_uuid: str
    ) -> List[ServiceModuleRead]:
        """
        [PRIVATE] Gets available service modules of a specific type that the actor
        has permission to use within the given Workspace context.
        """
        workspace = await self.workspace_dao.get_by_uuid(workspace_uuid)
        if not workspace:
            raise NotFoundError("Context workspace not found.")

        stmt = (
            select(ServiceModule)
            .join(ServiceModule.type)
            .join(ServiceModule.versions.and_(ServiceModuleVersion.status == ServiceModuleStatus.AVAILABLE))
            .where(ServiceModuleType.name == module_type)
            .options(
                joinedload(ServiceModule.permission),
                contains_eager(ServiceModule.versions)
            )
            .order_by(ServiceModule.id)
        )
        result = await self.db.execute(stmt)
        # .unique() 确保每个 ServiceModule 对象只出现一次
        candidate_modules = result.scalars().unique().all()
        
        available_modules: List[ServiceModule] = []
        for module in candidate_modules:
            # [CRITICAL] Pass the workspace as the context target
            if module.permission and await self.context.perm_evaluator.can([module.permission.name], target=workspace):
                if module.versions:
                    available_modules.append(module)
        
        return [ServiceModuleRead.model_validate(m) for m in available_modules]

    # ===================================================================
    # RUNTIME LOGIC (HARDENED)
    # ===================================================================
    async def get_runtime_context(
        self, 
        version_id: int, 
        actor: User, 
        workspace: Workspace
    ) -> ModuleRuntimeContext:
        """
        [HARDENED] Atomically loads a version, verifies its status, authorizes its use, 
        and resolves its credential.
        """
        # 1. Load the version with its parent module
        version = await self.smv_dao.get_one(
            where={"id": version_id},
            withs=[{
                "name": "service_module",
                "withs": ["permission"] # Eager load ServiceModule's permission
            }, "features"]
        )
        if not (version and version.service_module):
            raise NotFoundError(f"ServiceModuleVersion with id {version_id} not found or is misconfigured.")

        # 2. [HARDENING] Verify the operational status
        if version.status != ServiceModuleStatus.AVAILABLE:
            raise ServiceException(f"Service module version '{version.name}' is currently not available (Status: {version.status.value}).")

        # 3. Authorize
        perm_name = version.service_module.permission.name
        await self.context.perm_evaluator.ensure_can([perm_name], target=workspace)
        
        # 4. Resolve Credential
        credential = await self.credential_provider.get_credential(
            service_module=version.service_module,
            workspace=workspace
        )
        
        # 5. [HARDENING] Check for mandatory credentials
        if version.service_module.requires_credential and not credential:
            raise ServiceException(
                f"Execution of module '{version.service_module.name}' requires a credential (API Key), but none was found for this workspace or the platform."
            )
        
        # 6. Return the trusted context
        return ModuleRuntimeContext(
            module=version.service_module,
            version=version,
            features=version.features,
            credential=credential
        )