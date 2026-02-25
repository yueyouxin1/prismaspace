# app/services/workspace/workspace_service.py

from sqlalchemy.ext.asyncio import AsyncSession
from app.core.context import AppContext
from app.models import User, Workspace, Team, WorkspaceStatus
from app.dao.workspace.workspace_dao import WorkspaceDao
from app.dao.identity.team_dao import TeamDao
from app.schemas.workspace.workspace_schemas import WorkspaceRead, WorkspaceCreate, WorkspaceUpdate, OwnerInfo
from app.services.base_service import BaseService
from app.services.exceptions import ServiceException, NotFoundError, PermissionDeniedError

class WorkspaceService(BaseService):
    def __init__(self, context: AppContext):
        self.context = context
        self.db = context.db
        self.dao = WorkspaceDao(context.db)
        self.team_dao = TeamDao(context.db)

    # --- Public DTO-returning "Wrapper" Methods ---
    async def list_workspaces(self, actor: User) -> list[WorkspaceRead]:
        """获取用户有权访问的所有工作空间列表，返回DTO格式。"""
        workspaces = await self._list_workspaces(actor)
        return [WorkspaceRead.model_validate(w) for w in workspaces]

    async def get_workspace_by_uuid(self, workspace_uuid: str, actor: User) -> WorkspaceRead:
        """获取单个工作空间的详细信息，返回DTO格式。"""
        workspace = await self._get_workspace_by_uuid(workspace_uuid, actor)
        return WorkspaceRead.model_validate(workspace)

    async def create_workspace_for_team(self, workspace_data: WorkspaceCreate, actor: User) -> WorkspaceRead:
        """为团队创建一个新的工作空间，返回DTO格式。"""
        workspace = await self._create_workspace_for_team(workspace_data, actor)
        return WorkspaceRead.model_validate(workspace)

    async def update_workspace_by_uuid(self, workspace_uuid: str, update_data: WorkspaceUpdate, actor: User) -> WorkspaceRead:
        """更新一个已存在的工作空间，返回DTO格式。"""
        workspace = await self._update_workspace_by_uuid(workspace_uuid, update_data, actor)
        return WorkspaceRead.model_validate(workspace)

    async def archive_workspace_by_uuid(self, workspace_uuid: str, actor: User) -> None:
        """Delete a workspace (no return value)"""
        await self._archive_workspace_by_uuid(workspace_uuid, actor)

    # --- Internal ORM-returning "Workhorse" Method ---
    async def _list_workspaces(self, actor: User) -> list[Workspace]:
        """内部方法：获取用户有权访问的所有工作空间列表。"""
        workspaces = await self.dao.get_all_for_user(actor.id)
        return workspaces

    async def _get_workspace_by_uuid(self, workspace_uuid: str, actor: User) -> Workspace:
        """内部方法：获取单个工作空间的详细信息。"""
        workspace = await self.dao.get_one(
            where={"uuid": workspace_uuid}, 
            withs=["user_owner", "team"] # 预加载owner信息
        )
        if not workspace:
            raise NotFoundError("Workspace not found.")
        
        await self.context.perm_evaluator.ensure_can(
            permissions=["workspace:read"],
            target=workspace
        )
        
        return workspace

    async def _create_workspace_for_team(self, workspace_data: WorkspaceCreate, actor: User) -> Workspace:
        """内部方法：为团队创建一个新的工作空间。"""
        team_uuid = workspace_data.owner_team_uuid
        
        # 1. 业务验证：确保团队存在
        team = await self.team_dao.get_one(where={"uuid": team_uuid})
        if not team:
            raise NotFoundError(f"Team with uuid {team_uuid} not found.")

        # 2. 权限验证：用户必须有在该特定团队中创建工作空间的权限
        await self.context.perm_evaluator.ensure_can(
            permissions=["workspace:create"], 
            target=team
        )
        
        # 3. 创建对象
        new_workspace = Workspace(
            name=workspace_data.name,
            avatar=workspace_data.avatar,
            owner_team_id=team.id # [关键] 使用从DAO获取的 team.id
        )
        
        self.db.add(new_workspace)
        await self.db.flush()
        final_workspace = await self.dao.get_one(
            where={"id": new_workspace.id}
        )
        return final_workspace

    async def _update_workspace_by_uuid(self, workspace_uuid: str, update_data: WorkspaceUpdate, actor: User) -> Workspace:
        """内部方法：更新一个已存在的工作空间。"""
        workspace = await self.dao.get_by_uuid(workspace_uuid)
        if not workspace:
            raise NotFoundError("Workspace not found.")
            
        await self.context.perm_evaluator.ensure_can(
            permissions=["workspace:update"],
            target=workspace
        )

        workspace.name = update_data.name
        workspace.avatar = update_data.avatar
        
        await self.db.flush()
        await self.db.refresh(workspace)
        return workspace

    async def _archive_workspace_by_uuid(self, workspace_uuid: str, actor: User) -> None:
        """归档一个工作空间（软删除）。"""
        workspace = await self.dao.get_by_uuid(workspace_uuid)
        if not workspace:
            raise NotFoundError("Workspace not found.")
        
        # 归档是危险操作，需要更高级别的权限
        await self.context.perm_evaluator.ensure_can(
            permissions=["workspace:delete"],
            target=workspace
        )
        
        if workspace.owner_user_id:
             raise ServiceException("Personal workspace cannot be archived.")
             
        await self.dao.update_where(
            where={"id": workspace.id},
            values={"status": WorkspaceStatus.ARCHIVED}
        )