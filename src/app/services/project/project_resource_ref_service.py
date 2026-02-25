# src/app/services/project/project_resource_ref_service.py

from typing import List
from app.core.context import AppContext
from app.models import User
from app.models.resource import ProjectResourceRef
from app.dao.project.project_dao import ProjectDao
from app.dao.project.project_resource_ref_dao import ProjectResourceRefDao
from app.dao.resource.resource_dao import ResourceDao
from app.schemas.project.project_resource_schemas import (
    ProjectResourceReferenceCreate,
    ProjectResourceReferenceRead,
)
from app.services.exceptions import NotFoundError, ServiceException


class ProjectResourceRefService:
    def __init__(self, context: AppContext):
        self.context = context
        self.db = context.db
        self.project_dao = ProjectDao(context.db)
        self.resource_dao = ResourceDao(context.db)
        self.ref_dao = ProjectResourceRefDao(context.db)

    async def add_reference(
        self, project_uuid: str, ref_data: ProjectResourceReferenceCreate, actor: User
    ) -> ProjectResourceReferenceRead:
        project = await self.project_dao.get_by_uuid(project_uuid, withs=["workspace"])
        if not project:
            raise NotFoundError("Project not found.")

        await self.context.perm_evaluator.ensure_can(["project:update"], target=project.workspace)

        resource = await self.resource_dao.get_by_uuid(ref_data.resource_uuid, withs=["workspace"])
        if not resource:
            raise NotFoundError("Resource not found.")

        await self.context.perm_evaluator.ensure_can(["resource:read"], target=resource.workspace)

        if resource.workspace_id != project.workspace_id:
            raise ServiceException("Resource must belong to the same workspace as the project.")

        existing = await self.ref_dao.get_by_project_and_resource(project.id, resource.id)
        if existing:
            raise ServiceException("Resource already referenced by this project.")

        new_ref = ProjectResourceRef(
            project_id=project.id,
            resource_id=resource.id,
            alias=ref_data.alias,
            options=ref_data.options,
        )
        self.db.add(new_ref)
        await self.db.flush()
        final_ref = await self.ref_dao.get_by_project_and_resource(project.id, resource.id)
        if not final_ref:
            raise NotFoundError("Project resource reference not found.")
        return ProjectResourceReferenceRead.model_validate(final_ref)

    async def list_references(
        self, project_uuid: str, actor: User
    ) -> List[ProjectResourceReferenceRead]:
        project = await self.project_dao.get_by_uuid(project_uuid, withs=["workspace"])
        if not project:
            raise NotFoundError("Project not found.")

        await self.context.perm_evaluator.ensure_can(["project:read"], target=project.workspace)
        refs = await self.ref_dao.list_by_project_id(project.id)
        return [ProjectResourceReferenceRead.model_validate(ref) for ref in refs]

    async def remove_reference(self, project_uuid: str, resource_uuid: str, actor: User) -> None:
        project = await self.project_dao.get_by_uuid(project_uuid, withs=["workspace"])
        if not project:
            raise NotFoundError("Project not found.")

        await self.context.perm_evaluator.ensure_can(["project:update"], target=project.workspace)

        resource = await self.resource_dao.get_by_uuid(resource_uuid)
        if not resource:
            raise NotFoundError("Resource not found.")

        ref = await self.ref_dao.get_by_project_and_resource(project.id, resource.id)
        if not ref:
            raise NotFoundError("Project resource reference not found.")

        if project.main_resource_id == resource.id:
            project.main_resource_id = None
            await self.db.flush()

        await self.db.delete(ref)
        await self.db.flush()
