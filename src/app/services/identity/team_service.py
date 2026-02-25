# app/services/identity/team_service.py

from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.context import AppContext
from app.models import User, Team, TeamMember, BillingAccount, Currency
from app.dao.identity.team_dao import TeamDao
from app.dao.identity.team_member_dao import TeamMemberDao
from app.dao.permission.role_dao import RoleDao
from app.schemas.identity.team_schemas import TeamRead, TeamCreate, TeamUpdate, TeamMemberRead
from app.services.base_service import BaseService
from app.services.exceptions import ServiceException, ConfigurationError, NotFoundError
from app.core.config import settings

class TeamService(BaseService):
    def __init__(self, context: AppContext):
        self.context = context
        self.db = context.db
        self.team_dao = TeamDao(context.db)
        self.team_member_dao = TeamMemberDao(context.db)
        self.role_dao = RoleDao(context.db)

    # --- Public DTO-returning "Wrapper" Methods ---
    async def create_team(self, team_data: TeamCreate, actor: User) -> TeamRead:
        """创建新团队并返回DTO"""
        new_team = await self._create_team(team_data, actor)
        return TeamRead.model_validate(new_team)

    async def get_team_by_uuid(self, team_uuid: str, actor: User) -> TeamRead:
        """通过UUID获取团队并返回DTO"""
        team = await self._get_team_by_uuid(team_uuid, actor)
        return TeamRead.model_validate(team)

    async def get_teams_for_user(self, actor: User) -> list[TeamRead]:
        """获取用户所属的所有团队列表并返回DTO列表"""
        teams = await self._get_teams_for_user(actor)
        return [TeamRead.model_validate(t) for t in teams]

    async def update_team_by_uuid(self, team_uuid: str, team_data: TeamUpdate, actor: User) -> TeamRead:
        """更新团队信息并返回更新后的DTO"""
        team = await self._update_team_by_uuid(team_uuid, team_data, actor)
        return TeamRead.model_validate(team)

    async def delete_team_by_uuid(self, team_uuid: str, actor: User) -> None:
        """删除团队并返回操作确认"""
        await self._delete_team_by_uuid(team_uuid, actor)

    async def get_team_members(self, team_uuid: str, actor: User) -> list[TeamMemberRead]:
        """获取团队成员列表并返回DTO列表"""
        members = await self._get_team_members(team_uuid, actor)
        return [TeamMemberRead.model_validate(m) for m in members]

    async def remove_team_member(self, team_uuid: str, member_to_remove_uuid: str, actor: User) -> None:
        """移除团队成员并返回操作确认"""
        await self._remove_team_member(team_uuid, member_to_remove_uuid, actor)

    # --- Internal ORM-returning "Workhorse" Method ---
    async def _create_team(self, team_data: TeamCreate, actor: User) -> Team:
        """创建一个新团队。"""
        # 1. 权限检查: 检查 actor 是否有创建团队的平台级权限
        await self.context.perm_evaluator.ensure_can(
            permissions=["team:create"],
            target=actor
        )

        # 2. 查找 Owner 角色
        owner_role = await self.role_dao.get_system_role_by_name("team:owner")
        if not owner_role:
            raise ConfigurationError("System role 'team:owner' not found. Please run db seed.")

        # 3. 原子性地构建和持久化对象
        new_billing_account = BillingAccount(currency=Currency(settings.SITE_CURRENCY))
        new_team = Team(
            name=team_data.name,
            avatar=team_data.avatar,
            owner_id=actor.id,
            billing_account=new_billing_account
        )
        owner_membership = TeamMember(
            user_id=actor.id,
            team=new_team,
            role_id=owner_role.id
        )

        self.db.add_all([new_team, owner_membership])
        await self.db.flush()
        await self.db.refresh(new_team)
        fresh_team = await self.team_dao.get_one(
            where={"id": new_team.id},
            withs=["owner"] # 预加载owner关系
        )
        return fresh_team

    async def _get_team_by_uuid(self, team_uuid: str, actor: User) -> Team:
        """通过UUID获取单个团队，并进行权限检查。"""
        team = await self.team_dao.get_one(where={"uuid": team_uuid}, withs=["owner"])
        if not team:
            raise NotFoundError("Team not found.")

        # 权限检查: 检查 actor 是否有查看该特定团队的权限
        await self.context.perm_evaluator.ensure_can(
            permissions=["team:read"],
            target=team
        )
        return team

    async def _get_teams_for_user(self, actor: User) -> list[Team]:
        """获取用户所属的所有团队列表。"""
        # DAO层负责获取数据，权限由业务逻辑决定（能查到的就是有权限的）
        return await self.team_dao.get_all_for_user(actor.id)

    async def _update_team_by_uuid(self, team_uuid: str, team_data: TeamUpdate, actor: User) -> Team:
        """通过UUID更新团队信息。"""
        team = await self.team_dao.get_one(where={"uuid": team_uuid})
        if not team:
            raise NotFoundError("Team not found.")

        # 权限检查: 检查 actor 是否有更新该特定团队的权限
        await self.context.perm_evaluator.ensure_can(
            permissions=["team:update"],
            target=team
        )
        
        team.name = team_data.name
        team.avatar = team_data.avatar
        await self.db.flush()
        await self.db.refresh(team)
        
        return team

    async def _delete_team_by_uuid(self, team_uuid: str, actor: User) -> None:
        """通过UUID删除团队（高危操作）。"""
        team = await self.team_dao.get_one(where={"uuid": team_uuid})
        if not team:
            raise NotFoundError("Team not found.")
        
        # 权限检查: 检查 actor 是否有删除该特定团队的权限
        await self.context.perm_evaluator.ensure_can(
            permissions=["team:delete"],
            target=team
        )
        
        # SQLAlchemy的级联删除会自动处理TeamMember, Workspace等关联记录
        await self.db.delete(team)
        await self.db.flush()

    async def _get_team_members(self, team_uuid: str, actor: User) -> List[TeamMember]:
        """获取团队成员列表。"""
        team = await self.team_dao.get_one(where={"uuid": team_uuid})
        if not team:
            raise NotFoundError("Team not found.")

        # 权限检查: 只有团队成员才能查看成员列表
        await self.context.perm_evaluator.ensure_can(
            permissions=["team:member:read"],
            target=team
        )

        return await self.team_member_dao.get_members_by_team_id(team.id)

    async def _remove_team_member(self, team_uuid: str, member_to_remove_uuid: str, actor: User):
        """从团队中移除一个成员关系。"""
        team = await self.team_dao.get_one(where={"uuid": team_uuid})
        if not team:
            raise NotFoundError("Team not found.")

        # [修改] 通过 member_uuid 来精确查找成员关系记录
        member_to_remove = await self.team_member_dao.get_one(
            where={"uuid": member_to_remove_uuid, "team_id": team.id}
        )
        
        if not member_to_remove:
            raise NotFoundError("Team membership record not found.")

        # [关键业务规则] 不能移除团队所有者
        if member_to_remove.user_id == team.owner_id:
            raise ServiceException("Cannot remove the team owner.")
        
        # [关键业务规则] 用户不能移除自己（通常需要一个单独的“离开团队”接口）
        if member_to_remove.user_id == actor.id:
            raise ServiceException("You cannot remove yourself from the team. Please use the 'Leave Team' feature.")

        # ... (权限检查逻辑不变) ...
        await self.context.perm_evaluator.ensure_can(
            permissions=["team:member:remove"],
            target=team
        )
        
        await self.db.delete(member_to_remove)
        await self.db.flush()