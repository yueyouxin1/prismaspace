# app/services/project/project_service.py

from typing import List, Optional
from app.core.context import AppContext
from app.models import User, Project
from app.models.resource import Resource, ProjectResourceRef, VersionStatus
from app.dao.project.project_dao import ProjectDao
from app.dao.workspace.workspace_dao import WorkspaceDao
from app.dao.resource.resource_type_dao import ResourceTypeDao
from app.schemas.project.project_schemas import ProjectCreate, ProjectUpdate, ProjectRead
from app.schemas.project.project_env_schemas import ProjectEnvConfigRead, ProjectEnvConfigUpdate
from app.services.resource.base.base_resource_service import BaseResourceService
from app.services.exceptions import NotFoundError, ServiceException


class ProjectService(BaseResourceService):
    def __init__(self, context: AppContext):
        super().__init__(context)
        self.dao = ProjectDao(context.db)
        self.workspace_dao = WorkspaceDao(context.db)
        self.resource_type_dao = ResourceTypeDao(context.db)

    # --- Public DTO-returning "Wrapper" Method ---
    async def create_project_in_workspace(self, workspace_uuid: str, project_data: ProjectCreate, actor: User) -> ProjectRead:
        """Create a new project in the specified workspace and return as DTO"""
        project = await self._create_project_in_workspace(workspace_uuid, project_data, actor)
        return ProjectRead.model_validate(project)

    async def get_projects_in_workspace(
        self,
        workspace_uuid: str,
        actor: User,
        main_application_type: Optional[str] = None
    ) -> List[ProjectRead]:
        """Get all projects in the specified workspace as DTOs"""
        projects = await self._get_projects_in_workspace(workspace_uuid, actor, main_application_type)
        return [ProjectRead.model_validate(p) for p in projects]

    async def get_project_by_uuid(self, project_uuid: str, actor: User) -> ProjectRead:
        """Get a single project by UUID as DTO"""
        project = await self._get_project_by_uuid(project_uuid, actor)
        return ProjectRead.model_validate(project)

    async def update_project_by_uuid(self, project_uuid: str, update_data: ProjectUpdate, actor: User) -> ProjectRead:
        """Update a project and return the updated version as DTO"""
        project = await self._update_project_by_uuid(project_uuid, update_data, actor)
        return ProjectRead.model_validate(project)

    async def delete_project_by_uuid(self, project_uuid: str, actor: User) -> None:
        """Delete a project (no return value)"""
        await self._delete_project_by_uuid(project_uuid, actor)

    async def get_project_env_config(self, project_uuid: str, actor: User) -> ProjectEnvConfigRead:
        project = await self._get_project_by_uuid(project_uuid, actor)
        return ProjectEnvConfigRead.model_validate(project)

    async def update_project_env_config(
        self,
        project_uuid: str,
        update_data: ProjectEnvConfigUpdate,
        actor: User
    ) -> ProjectEnvConfigRead:
        project = await self._update_project_env_config(project_uuid, update_data, actor)
        return ProjectEnvConfigRead.model_validate(project)

    async def clear_project_env_config(self, project_uuid: str, actor: User) -> None:
        await self._clear_project_env_config(project_uuid, actor)

    # --- Internal ORM-returning "Workhorse" Method ---
    async def _create_project_in_workspace(self, workspace_uuid: str, project_data: ProjectCreate, actor: User) -> Project:
        workspace = await self.workspace_dao.get_by_uuid(workspace_uuid)
        if not workspace:
            raise NotFoundError("Workspace not found.")
        
        # 权限检查: 检查actor是否对该workspace有创建项目的权限
        await self.context.perm_evaluator.ensure_can(["project:create"], target=workspace)

        resource_type = await self.resource_type_dao.get_one(where={"name": project_data.main_application_type})
        if not resource_type:
            raise ServiceException(f"Unsupported main application type: {project_data.main_application_type}")
        if not resource_type.is_application:
            raise ServiceException("Main application type must be marked as application resource.")

        async with self.db.begin_nested():
            new_project = Project(
                **project_data.model_dump(exclude={"main_application_type"}),
                workspace_id=workspace.id,
                creator_id=actor.id
            )
            self.db.add(new_project)
            await self.db.flush()

            main_resource = Resource(
                name=f"{new_project.name} Main",
                description=new_project.description,
                workspace_id=workspace.id,
                resource_type_id=resource_type.id,
                creator_id=actor.id
            )
            self.db.add(main_resource)
            await self.db.flush()
            await self.db.refresh(main_resource)

            impl_service = await self._get_impl_service_by_type(resource_type.name)
            main_instance = await impl_service.create_instance(resource=main_resource, actor=actor)

            main_resource.workspace_instance = main_instance
            if main_instance.status == VersionStatus.PUBLISHED:
                main_resource.latest_published_instance = main_instance

            new_project.main_resource_id = main_resource.id
            self.db.add(
                ProjectResourceRef(
                    project_id=new_project.id,
                    resource_id=main_resource.id,
                    alias="main",
                )
            )
            await self.db.flush()

        final_project = await self.dao.get_one(
            where={"id": new_project.id},
            withs=[
                "creator",
                {"name": "main_resource", "withs": ["resource_type"]},
            ]
        )
        if not final_project:
            raise NotFoundError("Project not found after creation.")
        return final_project

    async def _get_projects_in_workspace(
        self,
        workspace_uuid: str,
        actor: User,
        main_application_type: Optional[str] = None
    ) -> List[Project]:
        workspace = await self.workspace_dao.get_by_uuid(workspace_uuid)
        if not workspace:
            raise NotFoundError("Workspace not found.")

        await self.context.perm_evaluator.ensure_can(["project:read"], target=workspace)

        return await self.dao.get_projects_by_workspace_id(workspace.id, main_application_type)

    async def _get_project_by_uuid(self, project_uuid: str, actor: User) -> Project:
        # 预加载 workspace 以便权限检查
        project = await self.dao.get_by_uuid(
            project_uuid,
            withs=[
                "workspace",
                "creator",
                {"name": "main_resource", "withs": ["resource_type"]},
            ],
        )
        if not project:
            raise NotFoundError("Project not found.")
            
        await self.context.perm_evaluator.ensure_can(["project:read"], target=project.workspace)
        
        return project

    async def _update_project_by_uuid(self, project_uuid: str, update_data: ProjectUpdate, actor: User) -> Project:
        project = await self.dao.get_by_uuid(
            project_uuid,
            withs=[
                "workspace",
                "creator",
                {"name": "main_resource", "withs": ["resource_type"]},
            ],
        )
        if not project:
            raise NotFoundError("Project not found.")

        await self.context.perm_evaluator.ensure_can(["project:update"], target=project.workspace)
        
        for key, value in update_data.model_dump().items():
            setattr(project, key, value)
            
        await self.db.flush()
        await self.db.refresh(project)
        final_project = await self.dao.get_one(
            where={"id": project.id},
            withs=[
                "workspace",
                "creator",
                {"name": "main_resource", "withs": ["resource_type"]},
            ],
        )
        if not final_project:
            raise NotFoundError("Project not found after update.")
        return final_project
        
    async def _delete_project_by_uuid(self, project_uuid: str, actor: User) -> None:
        project = await self.dao.get_by_uuid(project_uuid)
        if not project:
            raise NotFoundError("Project not found.")
            
        await self.context.perm_evaluator.ensure_can(["project:delete"], target=project.workspace)
        
        await self.db.delete(project)
        await self.db.flush()

    async def _update_project_env_config(
        self,
        project_uuid: str,
        update_data: ProjectEnvConfigUpdate,
        actor: User
    ) -> Project:
        project = await self.dao.get_by_uuid(project_uuid)
        if not project:
            raise NotFoundError("Project not found.")

        await self.context.perm_evaluator.ensure_can(["project:update"], target=project.workspace)

        project.env_config = update_data.env_config
        await self.db.flush()
        await self.db.refresh(project)
        return project

    async def _clear_project_env_config(self, project_uuid: str, actor: User) -> None:
        project = await self.dao.get_by_uuid(project_uuid)
        if not project:
            raise NotFoundError("Project not found.")

        await self.context.perm_evaluator.ensure_can(["project:update"], target=project.workspace)

        project.env_config = {}
        await self.db.flush()
