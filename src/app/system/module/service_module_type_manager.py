# src/app/system/module/service_module_type_manager.py

from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from app.models import ServiceModuleType
from app.dao.module.service_module_dao import ServiceModuleDao, ServiceModuleTypeDao
from app.schemas.module.service_module_type_schemas import ServiceModuleTypeCreate, ServiceModuleTypeUpdate
from app.services.exceptions import ServiceException, NotFoundError

class ServiceModuleTypeManager:
    """[System Layer] Manages the core business logic for ServiceModuleTypes."""
    def __init__(self, db: AsyncSession):
        self.db = db
        self.dao = ServiceModuleTypeDao(db)

    async def create_type(self, type_data: ServiceModuleTypeCreate) -> ServiceModuleType:
        existing_type = await self.dao.get_one(where={"name": type_data.name})
        if existing_type:
            raise ServiceException(f"ServiceModuleType with name '{type_data.name}' already exists.")
        
        new_type = ServiceModuleType(**type_data.model_dump())
        return await self.dao.add(new_type)

    async def get_type_by_name(self, name: str) -> ServiceModuleType:
        module_type = await self.dao.get_one(where={"name": name})
        if not module_type:
            raise NotFoundError(f"ServiceModuleType with name '{name}' not found.")
        return module_type
        
    async def list_types(self) -> List[ServiceModuleType]:
        return await self.dao.get_list()

    async def update_type(self, name: str, update_data: ServiceModuleTypeUpdate) -> ServiceModuleType:
        module_type = await self.get_type_by_name(name)
        
        update_dict = update_data.model_dump(exclude_unset=True)
        if not update_dict:
            return resource_type

        for key, value in update_dict.items():
            setattr(module_type, key, value)
            
        await self.db.flush()
        await self.db.refresh(module_type)
        return module_type

    async def delete_type(self, name: str) -> None:
        # Eagerly load the relationship to perform the pre-condition check
        module_type = await self.dao.get_one(where={"name": name}, withs=["service_modules"])
        if not module_type:
            raise NotFoundError(f"ServiceModuleType with name '{name}' not found.")

        # [CRITICAL] Pre-condition check before deletion
        existing_service_module = await ServiceModuleDao(self.db).get_one(where={"type_id": module_type.id})
        if existing_service_module:
            raise ServiceException(
                f"Cannot delete type '{name}' as it is still used by "
                f"modules including '{existing_service_module.name}'."
            )
        
        await self.db.delete(module_type)
        await self.db.flush()