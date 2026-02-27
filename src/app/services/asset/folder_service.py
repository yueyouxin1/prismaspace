from typing import Dict, List, Optional

from sqlalchemy import func, select

from app.core.context import AppContext
from app.dao.asset.folder_dao import AssetFolderDao
from app.dao.workspace.workspace_dao import WorkspaceDao
from app.models import Asset, AssetFolder, User
from app.schemas.asset.folder_schemas import (
    AssetFolderCreate,
    AssetFolderRead,
    AssetFolderTreeNodeRead,
    AssetFolderUpdate,
)
from app.services.base_service import BaseService
from app.services.exceptions import NotFoundError, ServiceException


class AssetFolderService(BaseService):
    def __init__(self, context: AppContext):
        self.db = context.db
        self.context = context
        self.dao = AssetFolderDao(context.db)
        self.workspace_dao = WorkspaceDao(context.db)

    async def _resolve_workspace(self, workspace_uuid: str):
        workspace = await self.workspace_dao.get_by_uuid(workspace_uuid)
        if not workspace:
            raise NotFoundError("Workspace not found.")
        return workspace

    async def _resolve_parent_folder(
        self,
        *,
        workspace_id: int,
        parent_uuid: Optional[str],
        parent_id: Optional[int],
    ) -> Optional[AssetFolder]:
        if not parent_uuid and parent_id is None:
            return None

        parent = None
        if parent_uuid:
            parent = await self.dao.get_active_by_uuid(parent_uuid, withs=["parent"])
        elif parent_id is not None:
            raw = await self.dao.get_by_pk(parent_id, withs=["parent"])
            if raw and not raw.is_deleted:
                parent = raw

        if not parent:
            raise NotFoundError("Parent folder not found.")
        if parent.workspace_id != workspace_id:
            raise ServiceException("Parent folder belongs to a different workspace.")
        return parent

    def _build_folder_tree(self, folders: List[AssetFolder]) -> List[AssetFolderTreeNodeRead]:
        by_parent: Dict[Optional[int], List[AssetFolder]] = {}
        for folder in folders:
            by_parent.setdefault(folder.parent_id, []).append(folder)

        def make_node(folder: AssetFolder) -> AssetFolderTreeNodeRead:
            children = sorted(by_parent.get(folder.id, []), key=lambda x: x.name.lower())
            return AssetFolderTreeNodeRead(
                id=folder.id,
                uuid=folder.uuid,
                name=folder.name,
                parent_uuid=getattr(folder.parent, "uuid", None),
                parent_id=folder.parent_id,
                created_at=folder.created_at,
                children=[make_node(child) for child in children],
            )

        roots = sorted(by_parent.get(None, []), key=lambda x: x.name.lower())
        return [make_node(root) for root in roots]

    def _collect_descendant_ids(self, folders: List[AssetFolder], root_id: int) -> List[int]:
        by_parent: Dict[int, List[int]] = {}
        for folder in folders:
            if folder.parent_id is None:
                continue
            by_parent.setdefault(folder.parent_id, []).append(folder.id)

        result: List[int] = []
        stack = [root_id]
        while stack:
            current = stack.pop()
            children = by_parent.get(current, [])
            result.extend(children)
            stack.extend(children)
        return result

    async def create_folder(self, workspace_uuid: str, data: AssetFolderCreate, actor: User) -> AssetFolderRead:
        workspace = await self._resolve_workspace(workspace_uuid)
        await self.context.perm_evaluator.ensure_can(["workspace:asset:create"], target=workspace)

        parent = await self._resolve_parent_folder(
            workspace_id=workspace.id,
            parent_uuid=data.parent_uuid,
            parent_id=data.parent_id,
        )
        parent_id = parent.id if parent else None
        name = data.name.strip()

        if await self.dao.has_name_conflict(workspace_id=workspace.id, parent_id=parent_id, name=name):
            raise ServiceException("A folder with the same name already exists in this parent.")

        folder = AssetFolder(name=name, parent_id=parent_id, workspace_id=workspace.id, creator_id=actor.id)
        self.db.add(folder)
        await self.db.flush()
        await self.db.refresh(folder)
        if parent:
            folder.parent = parent
        return AssetFolderRead.model_validate(folder)

    async def list_folders(
        self,
        workspace_uuid: str,
        actor: User,
        parent_uuid: Optional[str] = None,
        parent_id: Optional[int] = None,
    ) -> List[AssetFolderRead]:
        workspace = await self._resolve_workspace(workspace_uuid)
        await self.context.perm_evaluator.ensure_can(["workspace:asset:read"], target=workspace)

        if parent_uuid or parent_id is not None:
            parent = await self._resolve_parent_folder(
                workspace_id=workspace.id,
                parent_uuid=parent_uuid,
                parent_id=parent_id,
            )
            folders = await self.dao.list_children(parent.id)
        else:
            folders = await self.dao.list_roots(workspace.id)

        return [AssetFolderRead.model_validate(item) for item in folders]

    async def list_folder_tree(self, workspace_uuid: str, actor: User) -> List[AssetFolderTreeNodeRead]:
        workspace = await self._resolve_workspace(workspace_uuid)
        await self.context.perm_evaluator.ensure_can(["workspace:asset:read"], target=workspace)
        folders = await self.dao.list_by_workspace(workspace.id)
        return self._build_folder_tree(folders)

    async def update_folder(self, folder_uuid: str, data: AssetFolderUpdate, actor: User) -> AssetFolderRead:
        folder = await self.dao.get_active_by_uuid(folder_uuid, withs=["workspace", "parent"])
        if not folder:
            raise NotFoundError("Folder not found.")
        await self.context.perm_evaluator.ensure_can(["workspace:asset:update"], target=folder.workspace)

        name = folder.name
        if data.name is not None:
            stripped = data.name.strip()
            if not stripped:
                raise ServiceException("Folder name cannot be empty.")
            name = stripped

        next_parent_id = folder.parent_id
        next_parent = folder.parent
        if "parent_uuid" in data.model_fields_set or "parent_id" in data.model_fields_set:
            if data.parent_uuid is None and data.parent_id is None:
                next_parent = None
                next_parent_id = None
            else:
                next_parent = await self._resolve_parent_folder(
                    workspace_id=folder.workspace_id,
                    parent_uuid=data.parent_uuid,
                    parent_id=data.parent_id,
                )
                next_parent_id = next_parent.id

                if next_parent_id == folder.id:
                    raise ServiceException("Folder cannot be moved under itself.")

                all_folders = await self.dao.list_by_workspace(folder.workspace_id)
                descendant_ids = self._collect_descendant_ids(all_folders, folder.id)
                if next_parent_id in descendant_ids:
                    raise ServiceException("Folder cannot be moved under one of its descendants.")

        if await self.dao.has_name_conflict(
            workspace_id=folder.workspace_id,
            parent_id=next_parent_id,
            name=name,
            exclude_id=folder.id,
        ):
            raise ServiceException("A folder with the same name already exists in this parent.")

        folder.name = name
        folder.parent_id = next_parent_id
        folder.parent = next_parent
        await self.db.flush()
        return AssetFolderRead.model_validate(folder)

    async def delete_folder(self, folder_uuid: str, actor: User) -> None:
        folder = await self.dao.get_active_by_uuid(folder_uuid, withs=["workspace"])
        if not folder:
            raise NotFoundError("Folder not found.")

        await self.context.perm_evaluator.ensure_can(["workspace:asset:delete"], target=folder.workspace)

        children = await self.dao.list_children(folder.id)
        if children:
            raise ServiceException("Folder is not empty (contains subfolders).")

        asset_count = await self.context.db.scalar(
            select(func.count(Asset.id)).where(
                Asset.folder_id == folder.id,
                Asset.is_deleted == False,
            )
        )
        if int(asset_count or 0) > 0:
            raise ServiceException("Folder is not empty (contains assets).")

        await self.dao.soft_delete(folder.uuid)
