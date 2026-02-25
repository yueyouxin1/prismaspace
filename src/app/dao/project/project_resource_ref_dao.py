# src/app/dao/project/project_resource_ref_dao.py

from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload, load_only, lazyload
from app.dao.base_dao import BaseDao
from app.models.resource import ProjectResourceRef, Resource, ResourceInstance


def _instance_pointer_loader(relation_attr):
    return (
        selectinload(relation_attr)
        .options(
            load_only(
                ResourceInstance.id,
                ResourceInstance.uuid,
                ResourceInstance.resource_type,
                ResourceInstance.status
            ),
            lazyload(ResourceInstance.resource),
            lazyload(ResourceInstance.creator),
            lazyload(ResourceInstance.linked_feature),
        )
    )


class ProjectResourceRefDao(BaseDao[ProjectResourceRef]):
    def __init__(self, db_session):
        super().__init__(ProjectResourceRef, db_session)

    async def get_by_project_and_resource(self, project_id: int, resource_id: int) -> Optional[ProjectResourceRef]:
        stmt = (
            select(ProjectResourceRef)
            .where(
                ProjectResourceRef.project_id == project_id, 
                ProjectResourceRef.resource_id == resource_id
            )
            .options(
                joinedload(ProjectResourceRef.resource).joinedload(Resource.resource_type),
                joinedload(ProjectResourceRef.resource).joinedload(Resource.creator),
                joinedload(ProjectResourceRef.resource).options(
                    _instance_pointer_loader(Resource.workspace_instance),
                    _instance_pointer_loader(Resource.latest_published_instance),
                ),
            )
        )
        result = await self.db_session.execute(stmt)
        return result.scalars().first()

    async def list_by_project_id(self, project_id: int) -> List[ProjectResourceRef]:
        stmt = (
            select(ProjectResourceRef)
            .where(ProjectResourceRef.project_id == project_id)
            .options(
                joinedload(ProjectResourceRef.resource).joinedload(Resource.resource_type),
                joinedload(ProjectResourceRef.resource).joinedload(Resource.creator),
                joinedload(ProjectResourceRef.resource).options(
                    _instance_pointer_loader(Resource.workspace_instance),
                    _instance_pointer_loader(Resource.latest_published_instance),
                ),
            )
            .order_by(ProjectResourceRef.created_at.desc())
        )
        result = await self.db_session.execute(stmt)
        return result.scalars().unique().all()
