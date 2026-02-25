# src/app/services/permission/permission_service.py

from typing import List
from app.core.context import AppContext
from app.services.base_service import BaseService
from app.services.exceptions import PermissionDeniedError, NotFoundError
from app.system.permission.permission_manager import PermissionManager
from app.schemas.permission.permission_schemas import PermissionCreate, PermissionUpdate, PermissionReadNode
from app.dao.identity.team_dao import TeamDao

class PermissionService(BaseService):
    def __init__(self, context: AppContext):
        self.context = context
        self.db = context.db
        self.manager = PermissionManager(context.db)

    async def get_permission_tree(self) -> List[PermissionReadNode]:
        """
        [Admin] 获取完整的权限树，用于系统管理后台。
        """
        await self.context.perm_evaluator.ensure_can(["platform:permission:manage"])
        # Manager 现在直接返回 DTO 树，无需再次验证
        return await self.manager.get_permission_tree()

    async def get_assignable_permission_tree_for_team(self, team_uuid: str) -> List[PermissionReadNode]:
        """
        获取一个团队可用的、可分配的权限树。
        """
        team_dao = TeamDao(self.db) 
        team = await team_dao.get_by_uuid(team_uuid)
        if not team:
            raise NotFoundError("Team not found.")

        await self.context.perm_evaluator.ensure_can(["team:role:read"], target=team)
        
        # Manager 直接返回 DTO 树
        return await self.manager.get_assignable_permission_tree()

    async def create_permission(self, perm_data: PermissionCreate) -> PermissionReadNode:
        """[Admin] 创建一个新的权限定义。"""
        await self.context.perm_evaluator.ensure_can(["platform:permission:manage"])
        new_perm = await self.manager.create_permission(perm_data)
        return PermissionReadNode.model_validate(new_perm)
    
    async def update_permission(self, name: str, update_data: PermissionUpdate) -> PermissionReadNode:
        """[Admin] 更新一个权限定义。"""
        await self.context.perm_evaluator.ensure_can(["platform:permission:manage"])
        updated_perm = await self.manager.update_permission(name, update_data)
        return PermissionReadNode.model_validate(updated_perm)

    async def delete_permission(self, name: str) -> None:
        """[Admin] 删除一个权限定义。"""
        await self.context.perm_evaluator.ensure_can(["platform:permission:manage"])
        await self.manager.delete_permission(name)