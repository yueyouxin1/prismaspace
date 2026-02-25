# src/app/services/permission/permission_evaluator.py

import logging
from datetime import timedelta
from typing import List, Optional, Literal, Set, Any, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import attributes
from app.models import User, Workspace, Team, Role, ActionPermission, MembershipStatus, TeamMember
from app.services.redis_service import RedisService
from app.services.exceptions import PermissionDeniedError

class PermissionEvaluator:
    def __init__(
        self,
        db_session: AsyncSession,
        actor: User,
        redis_service: RedisService,
        permission_hierarchy: Dict[str, Set[str]]
    ):
        # [CRITICAL FIX] Fail-Fast Check
        if not permission_hierarchy:
            # This is a critical system failure. The application should not proceed
            # with an incomplete permission engine.
            logging.error("FATAL: PermissionEvaluator initialized with an empty or invalid hierarchy map.")
            raise RuntimeError("Permission hierarchy is not loaded, cannot perform secure permission checks.")
        self.db = db_session
        self.actor = actor
        self.redis_service = redis_service
        self.permission_hierarchy = permission_hierarchy
        self._permission_cache = {}
        self.CACHE_TTL = timedelta(minutes=5)

    async def ensure_can(
        self,
        permissions: List[str],
        target: User | Team | Workspace,
        logic: Literal["any", "all"] = "all"
    ):
        if not permissions: return
        current_actor = self.actor
        # [REFACTORED] Get the full, effective permission set
        effective_perms = await self._get_effective_permissions(current_actor, target)
        
        required_perms = set(permissions)
        
        # We must ensure that the permissions being requested are even valid permissions in the system.
        # And then get all their ancestors for the check.
        all_required_with_ancestors = set()
        for perm in required_perms:
            # If a requested permission doesn't exist in the hierarchy map, it's an invalid permission.
            if perm not in self.permission_hierarchy:
                # This could be a typo in the code calling ensure_can. It's a developer error.
                raise ValueError(f"Permission '{perm}' being checked does not exist in the loaded permission hierarchy.")
            
            all_required_with_ancestors.add(perm)
            all_required_with_ancestors.update(self.permission_hierarchy[perm])
        
        has_permission = False
        if logic == "any":
            # Check if the user has AT LEAST ONE of the required permission chains.
            # This is complex. The simplest secure interpretation is:
            # Does the user have *any* of the *specifically requested* permissions (including their parents)?
            # For each required permission, check if the user has it and its parents.
            for req_perm in permissions:
                required_chain = {req_perm, *self.permission_hierarchy.get(req_perm, set())}
                if effective_perms.issuperset(required_chain):
                    has_permission = True
                    break # Found one valid chain, that's enough for 'any'
        else: # logic == "all"
            if effective_perms.issuperset(all_required_with_ancestors): has_permission = True
        
        if not has_permission:
            raise PermissionDeniedError(f"Actor '{current_actor.uuid}' lacks required permissions: {', '.join(required_perms)}")

    async def can(
        self,
        permissions: List[str],
        target: User | Team | Workspace,
        logic: Literal["any", "all"] = "any"
    ) -> bool:
        """
        [ROBUST IMPLEMENTATION] Checks permissions without raising an exception.
        """
        try:
            await self.ensure_can(permissions=permissions, target=target, logic=logic)
            return True
        except PermissionDeniedError:
            return False
        except Exception as e:
            logging.error(f"Unexpected error during permission check 'can()': {e}", exc_info=True)
            return False

    async def invalidate_cache_for_user(self, user: User):
        prefix = self._get_user_cache_prefix(user)
        logging.info(f"Initiating verified cache invalidation for user_id: {user.id} with prefix '{prefix}'")
        
        # The call remains simple, but the action is now far more robust.
        await self.redis_service.delete_by_prefix(prefix)

        # Local cache invalidation logic remains the same.
        keys_to_del_local = [k for k in self._permission_cache.keys() if k.startswith(prefix)]
        for k in keys_to_del_local:
            del self._permission_cache[k]

    # ===================================================================
    # INTERNAL "PRIVATE" METHODS
    # ===================================================================

    def _expand_permissions(self, base_perms: Set[str]) -> Set[str]:
        """Expands a set of base permissions to include all their ancestors using the preloaded map."""
        expanded_perms = set(base_perms)
        for perm in base_perms:
            ancestors = self.permission_hierarchy.get(perm)
            if ancestors:
                expanded_perms.update(ancestors)
        return expanded_perms

    async def _get_base_permissions_from_roles(self, actor: User, target: User | Team | Workspace) -> Set[str]:
        """Fetches the set of permissions directly assigned to the actor's roles in a given context."""
        # 用户上下文：读取平台级 Membership 角色。
        if isinstance(target, User):
            user_role_id = await self._get_user_role_id(target)
            if not user_role_id:
                return set()
            stmt = (
                select(ActionPermission.name)
                .join(ActionPermission.roles)
                .where(Role.id == user_role_id)
            )
            result = await self.db.execute(stmt)
            return set(result.scalars().all())

        # 团队上下文：直接通过 TeamMember -> Role -> ActionPermission 一跳查询，避免先查 member 再二次查权限。
        if isinstance(target, Team):
            stmt = (
                select(ActionPermission.name)
                .select_from(TeamMember)
                .join(Role, TeamMember.role_id == Role.id)
                .join(Role.permissions)
                .where(
                    TeamMember.user_id == actor.id,
                    TeamMember.team_id == target.id
                )
            )
            result = await self.db.execute(stmt)
            return set(result.scalars().all())

        # 工作空间上下文：团队工作空间复用团队权限链路；个人工作空间回退用户角色。
        if isinstance(target, Workspace):
            if target.owner_team_id:
                stmt = (
                    select(ActionPermission.name)
                    .select_from(TeamMember)
                    .join(Role, TeamMember.role_id == Role.id)
                    .join(Role.permissions)
                    .where(
                        TeamMember.user_id == actor.id,
                        TeamMember.team_id == target.owner_team_id
                    )
                )
                result = await self.db.execute(stmt)
                return set(result.scalars().all())

            if target.owner_user_id == actor.id:
                user_role_id = await self._get_user_role_id(actor)
                if not user_role_id:
                    return set()
                stmt = (
                    select(ActionPermission.name)
                    .join(ActionPermission.roles)
                    .where(Role.id == user_role_id)
                )
                result = await self.db.execute(stmt)
                return set(result.scalars().all())

        return set()

    def _get_user_cache_prefix(self, actor: User) -> str:
        return f"perms:actor_{actor.id}::"

    async def _get_cache_key_for_context(self, actor: User, target: User | Team | Workspace) -> str:
        prefix = self._get_user_cache_prefix(actor)
        context_key = f"user_{actor.id}"
        if isinstance(target, User):
            context_key = f"user_{target.id}"
        elif isinstance(target, Team):
            context_key = f"team_{target.id}"
        elif isinstance(target, Workspace) and target.owner_team_id:
            context_key = f"team_{target.owner_team_id}"
        return f"{prefix}{context_key}"
                
    async def _get_effective_permissions(self, actor: User, target: User | Team | Workspace) -> Set[str]:
        """
        [NEW CORE LOGIC] The main method to get a user's complete, expanded permission set for a context.
        It orchestrates caching, fetching base permissions, and expansion.
        """
        cache_key = await self._get_cache_key_for_context(actor, target)
        
        # Check request-level in-memory cache first
        if cache_key in self._permission_cache:
            return self._permission_cache[cache_key]

        # Check shared Redis cache
        cached_perms = await self.redis_service.get_json(cache_key)
        if cached_perms is not None:
            effective_perms = set(cached_perms)
            self._permission_cache[cache_key] = effective_perms # Populate in-memory cache
            return effective_perms

        # --- Cache miss: perform the full calculation ---
        # 1. Get base permissions from roles
        base_perms = await self._get_base_permissions_from_roles(actor, target)
        
        # 2. Expand to include all ancestors
        effective_perms = self._expand_permissions(base_perms)
        
        # 3. Store in both caches for future requests
        await self.redis_service.set_json(cache_key, list(effective_perms), expire=self.CACHE_TTL)
        self._permission_cache[cache_key] = effective_perms
        
        return effective_perms

    async def _get_user_role_id(self, actor: User) -> Optional[int]: # 返回类型修正为 Optional[int]
        """
        获取用户的平台级角色ID。
        
        严格依赖于用户拥有一个有效的、激活的会员资格(Membership)。
        如果找不到，则返回 None，这将导致权限检查失败，从而暴露上游的业务逻辑问题。
        """
        # 确保 membership 关系已被加载
        if 'membership' in attributes.instance_state(actor).unloaded:
            await self.db.refresh(actor, ['membership'])

        # --- 主要路径: 检查有效的 Membership ---
        if actor.membership and actor.membership.status == MembershipStatus.ACTIVE:
            return actor.membership.role_id
        
        # --- 快速失败: 记录问题并返回 None ---
        logging.warning(
            f"User {actor.uuid} does not have an active membership. "
            "Permission evaluation will proceed with no platform-level roles. "
            "This might indicate an issue in the membership lifecycle management."
        )
        return None

