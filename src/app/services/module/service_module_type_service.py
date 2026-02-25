# src/app/services/module/service_module_type_service.py

from typing import List
from app.core.context import AppContext
from app.services.base_service import BaseService
from app.system.module.service_module_type_manager import ServiceModuleTypeManager
from app.schemas.module.service_module_type_schemas import ServiceModuleTypeCreate, ServiceModuleTypeUpdate, ServiceModuleTypeRead
from app.services.exceptions import PermissionDeniedError

class ServiceModuleTypeService(BaseService):
    """[Service Layer] Orchestrates ServiceModuleType management and handles authorization."""
    def __init__(self, context: AppContext):
        self.context = context
        self.manager = ServiceModuleTypeManager(context.db)
        # All methods in this service require the same high-level permission
        self.required_permission = "platform:servicemodule:manage"

    async def create_type(self, type_data: ServiceModuleTypeCreate) -> ServiceModuleTypeRead:
        await self.context.perm_evaluator.ensure_can([self.required_permission])
        new_type = await self.manager.create_type(type_data)
        return ServiceModuleTypeRead.model_validate(new_type)

    async def list_types(self) -> List[ServiceModuleTypeRead]:
        # Listing types can be a public or semi-public operation.
        # Let's make it require auth, but not admin rights for now.
        # This allows authenticated users (e.g., developers) to discover available types.
        types = await self.manager.list_types()
        return [ServiceModuleTypeRead.model_validate(t) for t in types]

    async def update_type(self, name: str, update_data: ServiceModuleTypeUpdate) -> ServiceModuleTypeRead:
        await self.context.perm_evaluator.ensure_can([self.required_permission])
        updated_type = await self.manager.update_type(name, update_data)
        return ServiceModuleTypeRead.model_validate(updated_type)

    async def delete_type(self, name: str) -> None:
        await self.context.perm_evaluator.ensure_can([self.required_permission])
        await self.manager.delete_type(name)