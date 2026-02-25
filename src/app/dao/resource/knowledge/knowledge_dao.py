# src/app/dao/resource/knowledge/knowledge_dao.py

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload, joinedload, load_only
from typing import Optional, List
from app.dao.base_dao import BaseDao

# Import the models this DAO will interact with
from app.models.identity import User, Team
from app.models.workspace import Workspace
from app.models.resource import Resource
from app.models.resource.knowledge import KnowledgeBase, KnowledgeDocument, KnowledgeChunk

class KnowledgeBaseDao(BaseDao[KnowledgeBase]):
    """DAO for KnowledgeBase resource instances."""
    def __init__(self, db_session: AsyncSession):
        super().__init__(model_class=KnowledgeBase, db_session=db_session)

    @staticmethod
    def _runtime_options() -> list:
        resource_loader = joinedload(KnowledgeBase.resource).options(
            lazyload("*"),
            load_only(
                Resource.id,
                Resource.uuid,
                Resource.workspace_id,
                Resource.resource_type_id
            ),
            joinedload(Resource.workspace).options(
                lazyload("*"),
                load_only(
                    Workspace.id,
                    Workspace.uuid,
                    Workspace.owner_user_id,
                    Workspace.owner_team_id
                ),
                joinedload(Workspace.user_owner).options(
                    joinedload(User.billing_account)
                ),
                joinedload(Workspace.team).options(
                    joinedload(Team.billing_account)
                ),
            )
        )
        return [
            lazyload("*"),
            resource_loader,
            joinedload(KnowledgeBase.creator).options(
                lazyload("*"),
                load_only(User.id, User.uuid, User.nick_name, User.avatar)
            ),
            joinedload(KnowledgeBase.embedding_module_version),
        ]

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[KnowledgeBase]:
        """Finds a KnowledgeBase instance by its ResourceInstance UUID."""
        return await self.get_one(
            where={"uuid": uuid},
            withs=withs,
            options=self._runtime_options()
        )

    async def get_by_pk(
        self,
        pk_value: int,
        joins: Optional[list] = None,
        withs: Optional[list] = None,
        fields: Optional[list[str]] = None,
        options: Optional[List] = None
    ) -> Optional[KnowledgeBase]:
        merged_options = [*self._runtime_options()]
        if options:
            merged_options.extend(options)
        return await super().get_by_pk(
            pk_value=pk_value,
            joins=joins,
            withs=withs,
            fields=fields,
            options=merged_options
        )

    async def get_by_uuids(self, uuids: List[str], withs: Optional[list] = None) -> Optional[List[KnowledgeBase]]:
        """Finds a KnowledgeBase instance by its ResourceInstance UUID."""
        return await self.get_list(
            where=[KnowledgeBase.uuid.in_(uuids)],
            withs=withs,
            options=self._runtime_options(),
            unique=True
        )

class KnowledgeDocumentDao(BaseDao[KnowledgeDocument]):
    """DAO for managing KnowledgeDocument records."""
    def __init__(self, db_session: AsyncSession):
        super().__init__(model_class=KnowledgeDocument, db_session=db_session)

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[KnowledgeDocument]:
        """Finds a KnowledgeDocument by its UUID."""
        return await self.get_one(where={"uuid": uuid}, withs=withs)


class KnowledgeChunkDao(BaseDao[KnowledgeChunk]):
    """DAO for managing KnowledgeChunk records."""
    def __init__(self, db_session: AsyncSession):
        super().__init__(model_class=KnowledgeChunk, db_session=db_session)

    async def get_by_uuid(self, uuid: str, withs: Optional[list] = None) -> Optional[KnowledgeChunk]:
        """Finds a KnowledgeChunk by its UUID."""
        return await self.get_one(where={"uuid": uuid}, withs=withs)

    async def get_by_vector_ids(self, vector_ids: List[str], withs: Optional[list] = None) -> List[KnowledgeChunk]:
        """
        [高效查询] Retrieves a list of KnowledgeChunk objects based on their vector_id.
        This is crucial for the "hydration" step in the search process.
        """
        if not vector_ids:
            return []
        
        # Use the 'in' operator for efficient batch fetching
        return await self.get_list(
            where=[self.model.vector_id.in_(vector_ids)],
            withs=withs
        )
