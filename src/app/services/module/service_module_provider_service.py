# src/app/services/module/service_module_provider_service.py

from typing import List
from app.core.context import AppContext
from app.services.base_service import BaseService
from app.system.module.service_module_provider_manager import ServiceModuleProviderManager
from app.schemas.module.service_module_provider_schemas import ServiceModuleProviderCreate, ServiceModuleProviderUpdate, ServiceModuleProviderRead
from app.services.exceptions import PermissionDeniedError

class ServiceModuleProviderService(BaseService):
    """[Service Layer] Orchestrates ServiceModuleProvider management and handles authorization."""
    def __init__(self, context: AppContext):
        self.context = context
        self.manager = ServiceModuleProviderManager(context.db)
        # All methods in this service require the same high-level permission
        self.required_permission = "platform:servicemodule:manage"

    async def create_provider(self, provider_data: ServiceModuleProviderCreate) -> ServiceModuleProviderRead:
        await self.context.perm_evaluator.ensure_can([self.required_permission])
        new_provider = await self.manager.create_provider(provider_data)
        return ServiceModuleProviderRead.model_validate(new_provider)

    async def list_providers(self) -> List[ServiceModuleProviderRead]:
        # Listing providers can be a public or semi-public operation.
        # Let's make it require auth, but not admin rights for now.
        # This allows authenticated users (e.g., developers) to discover available providers.
        providers = await self.manager.list_providers()
        return [ServiceModuleProviderRead.model_validate(t) for t in providers]

    async def update_provider(self, name: str, update_data: ServiceModuleProviderUpdate) -> ServiceModuleProviderRead:
        await self.context.perm_evaluator.ensure_can([self.required_permission])
        updated_provider = await self.manager.update_provider(name, update_data)
        return ServiceModuleProviderRead.model_validate(updated_provider)

    async def delete_provider(self, name: str) -> None:
        await self.context.perm_evaluator.ensure_can([self.required_permission])
        await self.manager.delete_provider(name)