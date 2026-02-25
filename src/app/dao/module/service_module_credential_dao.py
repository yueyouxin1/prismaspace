# src/app/dao/module/service_module_credential_dao.py

from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from app.dao.base_dao import BaseDao
from app.models.module import ServiceModuleCredential

class ServiceModuleCredentialDao(BaseDao[ServiceModuleCredential]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(ServiceModuleCredential, db_session)

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[ServiceModuleCredential]:
        """Finds a credential by its public UUID."""
        return await self.get_one(where={"uuid": uuid}, withs=withs)

    async def get_by_workspace_and_provider(
        self, provider_id: int, workspace_id: int
    ) -> Optional[ServiceModuleCredential]:
        """
        Finds the unique credential for a given provider within a specific workspace.
        """
        return await self.get_one(where={"provider_id": provider_id, "workspace_id": workspace_id})
        
    async def get_for_workspace(self, workspace_id: int) -> List[ServiceModuleCredential]:
        """Gets all credentials configured for a specific workspace."""
        return await self.get_list(where={"workspace_id": workspace_id})