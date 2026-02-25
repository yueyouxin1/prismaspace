# src/app/system/module/service_module_provider_manager.py

from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from app.models import ServiceModuleProvider
from app.dao.module.service_module_dao import ServiceModuleProviderDao
from app.dao.module.service_module_credential_dao import ServiceModuleCredentialDao
from app.schemas.module.service_module_provider_schemas import ServiceModuleProviderCreate, ServiceModuleProviderUpdate
from app.services.exceptions import ServiceException, NotFoundError

class ServiceModuleProviderManager:
    """[System Layer] Manages the core business logic for ServiceModuleProviders."""
    def __init__(self, db: AsyncSession):
        self.db = db
        self.dao = ServiceModuleProviderDao(db)

    async def create_provider(self, provider_data: ServiceModuleProviderCreate) -> ServiceModuleProvider:
        existing_provider = await self.dao.get_one(where={"name": provider_data.name})
        if existing_provider:
            raise ServiceException(f"ServiceModuleProvider with name '{provider_data.name}' already exists.")
        
        new_provider = ServiceModuleProvider(**provider_data.model_dump())
        return await self.dao.add(new_provider)

    async def get_provider_by_name(self, name: str) -> ServiceModuleProvider:
        module_provider = await self.dao.get_one(where={"name": name})
        if not module_provider:
            raise NotFoundError(f"ServiceModuleProvider with name '{name}' not found.")
        return module_provider
        
    async def list_providers(self) -> List[ServiceModuleProvider]:
        return await self.dao.get_list()

    async def update_provider(self, name: str, update_data: ServiceModuleProviderUpdate) -> ServiceModuleProvider:
        module_provider = await self.get_provider_by_name(name)
        
        update_dict = update_data.model_dump(exclude_unset=True)
        if not update_dict:
            return resource_provider

        for key, value in update_dict.items():
            setattr(module_provider, key, value)
            
        await self.db.flush()
        await self.db.refresh(module_provider)
        return module_provider

    async def delete_provider(self, name: str) -> None:
        # Eagerly load the relationship to perform the pre-condition check
        module_provider = await self.dao.get_one(where={"name": name})
        if not module_provider:
            raise NotFoundError(f"ServiceModuleProvider with name '{name}' not found.")

        # [CRITICAL] Pre-condition check before deletion
        existing_service_module = await ServiceModuleDao(self.db).get_one(where={"provider_id": module_provider.id})
        if existing_service_module:
            raise ServiceException(
                f"Cannot delete provider '{name}' as it is still used by "
                f"modules including '{existing_service_module.name}'."
            )
        existing_credential = await ServiceModuleCredentialDao(self.db).get_one(where={"provider_id": module_type.id})
        if existing_credential:
            raise ServiceException(
                f"Cannot delete provider '{name}' as it is still used by credentials."
            )

        await self.db.delete(module_provider)
        await self.db.flush()