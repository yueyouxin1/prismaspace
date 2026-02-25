# src/app/services/permission/role_service.py

from typing import List
from app.core.context import AppContext
from app.models import Team, Role
from app.dao.identity.team_dao import TeamDao
from app.dao.permission.role_dao import RoleDao
from app.schemas.permission.role_schemas import RoleCreate, RoleUpdate, RoleRead
from app.services.base_service import BaseService
from app.services.exceptions import NotFoundError
from app.system.permission.role_manager import RoleManager

class RoleService(BaseService):
    """
    [服务层] 负责角色管理的业务流程编排和权限检查。
    """
    def __init__(self, context: AppContext):
        self.context = context
        self.db = context.db
        self.team_dao = TeamDao(db)
        self.role_dao = RoleDao(db)
        self.manager = RoleManager(db)

    async def get_team_roles(self, team_uuid: str) -> List[RoleRead]:
        """获取一个团队的所有可自定义角色。"""
        team = await self.team_dao.get_by_uuid(team_uuid)
        if not team:
            raise NotFoundError("Team not found.")
        
        await self.context.perm_evaluator.ensure_can(["team:role:read"], target=team)
        
        roles = await self.role_dao.get_list(
            where={"team_id": team.id},
            withs=["permissions"]
        )
        return [RoleRead.model_validate(r) for r in roles]

    async def create_team_role(self, team_uuid: str, role_data: RoleCreate) -> RoleRead:
        """在一个团队中创建一个新的自定义角色。"""
        team = await self.team_dao.get_by_uuid(team_uuid)
        if not team:
            raise NotFoundError("Team not found.")
        
        await self.context.perm_evaluator.ensure_can(["team:role:write"], target=team)
        
        new_role = await self.manager.create_role(role_data, team_id=team.id)
        return RoleRead.model_validate(new_role)

    async def update_team_role(self, team_uuid: str, role_uuid: str, update_data: RoleUpdate) -> RoleRead:
        """更新一个团队的自定义角色。"""
        team = await self.team_dao.get_by_uuid(team_uuid)
        if not team:
            raise NotFoundError("Team not found.")
        
        await self.context.perm_evaluator.ensure_can(["team:role:write"], target=team)
        
        role_to_update = await self.role_dao.get_one(where={"uuid": role_uuid, "team_id": team.id})
        if not role_to_update:
            raise NotFoundError("Role not found in this team.")
            
        updated_role = await self.manager.update_role(role_to_update, update_data)
        return RoleRead.model_validate(updated_role)

    async def delete_team_role(self, team_uuid: str, role_uuid: str) -> None:
        """删除一个团队的自定义角色。"""
        team = await self.team_dao.get_by_uuid(team_uuid)
        if not team:
            raise NotFoundError("Team not found.")
        
        await self.context.perm_evaluator.ensure_can(["team:role:write"], target=team)
        
        role_to_delete = await self.role_dao.get_one(where={"uuid": role_uuid, "team_id": team.id})
        if not role_to_delete:
            raise NotFoundError("Role not found in this team.")
            
        await self.manager.delete_role(role_to_delete)