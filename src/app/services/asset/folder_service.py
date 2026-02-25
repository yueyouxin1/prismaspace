from typing import List, Optional, Union
from sqlalchemy import select, func
from app.core.context import AppContext
from app.models import User, Team, Asset, AssetFolder
from app.dao.asset.folder_dao import AssetFolderDao
from app.dao.workspace.workspace_dao import WorkspaceDao
from app.schemas.asset.folder_schemas import AssetFolderCreate, AssetFolderUpdate, AssetFolderRead
from app.services.base_service import BaseService
from app.services.exceptions import NotFoundError, PermissionDeniedError, ServiceException
        
class AssetFolderService(BaseService):
    def __init__(self, context: AppContext):
        self.db = context.db
        self.context = context
        self.dao = AssetFolderDao(context.db)
        self.workspace_dao = WorkspaceDao(context.db)

    async def create_folder(
        self, 
        workspace_uuid: str,
        data: AssetFolderCreate, 
        actor: User
    ) -> AssetFolderRead:
        
        workspace = await self.workspace_dao.get_by_uuid(workspace_uuid)
        if not workspace: raise NotFoundError("Workspace not found.")
        await self.context.perm_evaluator.ensure_can(["workspace:asset:create"], target=workspace)

        # Verify parent folder ownership if provided
        if data.parent_id:
            parent = await self.dao.get_by_pk(data.parent_id)
            if not parent or parent.is_deleted:
                raise NotFoundError("Parent folder not found")
            if parent.workspace_id != workspace.id:
                raise PermissionDeniedError("Parent folder belongs to a different workspace")

        folder = AssetFolder(
            name=data.name,
            parent_id=data.parent_id,
            workspace_id=workspace.id
        )
        self.db.add(folder)
        await self.db.flush()
        return AssetFolderRead.model_validate(folder)

    async def list_folders(
        self, 
        workspace_uuid: str,
        actor: User, 
        parent_id: Optional[int] = None
    ) -> List[AssetFolderRead]:
        
        workspace = await self.workspace_dao.get_by_uuid(workspace_uuid)
        if not workspace: raise NotFoundError("Workspace not found.")
        await self.context.perm_evaluator.ensure_can(["workspace:asset:read"], target=workspace)

        if parent_id:
            folders = await self.dao.list_children(parent_id)
        else:
            folders = await self.dao.list_roots(workspace.id)
            
        return [AssetFolderRead.model_validate(f) for f in folders]

    async def delete_folder(self, folder_uuid: str, actor: User) -> None:
        """
        Delete folder. Validates that folder is empty.
        """
        folder = await self.dao.get_active_by_uuid(folder_uuid, withs=["workspace"])
        if not folder: raise NotFoundError("Folder not found")

        # Auth check
        await self.context.perm_evaluator.ensure_can(["workspace:asset:delete"], target=folder.workspace)

        # Check for children folders
        children = await self.dao.list_children(folder.id)
        if children:
            raise ServiceException("Folder is not empty (contains subfolders).")
        
        asset_count = await self.context.db.scalar(
            select(func.count(Asset.id)).where(Asset.folder_id == folder.id, Asset.is_deleted == False)
        )
        if asset_count > 0:
            raise ServiceException("Folder is not empty (contains assets).")

        await self.dao.soft_delete(folder.uuid)