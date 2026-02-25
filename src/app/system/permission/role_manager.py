# src/app/system/permission/role_manager.py

from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional, Set
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload, attributes
from app.models import Role, ActionPermission, TeamMember, RoleType
from app.dao.permission.role_dao import RoleDao
from app.dao.permission.action_permission_dao import ActionPermissionDao
from app.schemas.permission.role_schemas import RoleCreate, RoleUpdate
from app.services.exceptions import ServiceException, NotFoundError

class RoleManager:
    """
    [System Layer - Snapshot Model] Manages the core business logic for Roles.
    It is responsible for calculating and storing the complete ("denormalized")
    permission set for each role upon creation and updates.
    """
    def __init__(self, db: AsyncSession):
        self.db = db
        self.role_dao = RoleDao(db)
        self.permission_dao = ActionPermissionDao(db)

    async def _resolve_permission_names_to_objects(self, perm_names: Set[str]) -> List[ActionPermission]:
        """[Helper] Safely converts a set of permission names into ORM objects."""
        if not perm_names:
            return []
        
        perm_objects = await self.permission_dao.get_list(
            where=[ActionPermission.name.in_(perm_names)]
        )
        if len(perm_objects) != len(perm_names):
            found_names = {p.name for p in perm_objects}
            missing = perm_names - found_names
            raise NotFoundError(f"The following permissions do not exist: {', '.join(missing)}")
        
        return perm_objects

    async def create_role(self, role_data: RoleCreate, team_id: Optional[int] = None) -> Role:
        if await self.role_dao.get_one(where={"name": role_data.name, "team_id": team_id}):
            scope = f"team ID {team_id}" if team_id else "the system"
            raise ServiceException(f"A role named '{role_data.name}' already exists in {scope}.")

        parent_id: Optional[int] = None
        inherited_permissions: Set[ActionPermission] = set()

        # 1. Resolve parent and get its full, already calculated permission set
        if role_data.parent_name:
            parent_role = await self.role_dao.get_one(
                where={"name": role_data.parent_name, "team_id": team_id},
                withs=["permissions"] # Eager load parent's FULL permissions
            )
            if not parent_role:
                raise NotFoundError(f"Parent role '{role_data.parent_name}' not found in the same scope.")
            parent_id = parent_role.id
            inherited_permissions = set(parent_role.permissions)

        # 2. Resolve the directly assigned permissions
        direct_permissions = await self._resolve_permission_names_to_objects(set(role_data.permissions))

        # 3. Combine inherited and direct permissions
        final_permission_set = inherited_permissions.union(set(direct_permissions))

        # 4. Create the role and assign the FINAL, complete permission set
        new_role = Role(
            name=role_data.name,
            label=role_data.label,
            description=role_data.description,
            team_id=team_id,
            parent_id=parent_id,
            permissions=list(final_permission_set) # Assign the full, denormalized set
        )
        return await self.role_dao.add(new_role)

    async def update_role(self, role_to_update: Role, update_data: RoleUpdate) -> Role:
        """
        Updates a role. If its direct permissions change, it triggers a recalculation
        for itself and all of its descendants.
        """
        update_dict = update_data.model_dump(exclude_unset=True)
        permissions_have_changed = 'permissions' in update_dict
        
        # We start a transaction here to ensure the entire update cascade is atomic
        async with self.db.begin_nested():
            # 1. Update basic fields on the target role
            role_to_update.label = update_data.label or role_to_update.label
            role_to_update.description = update_data.description or role_to_update.description
            
            # 2. If permissions changed, recalculate for this role first
            if permissions_have_changed:
                if 'permissions' in attributes.instance_state(role_to_update).unloaded:
                    await self.db.refresh(role_to_update, attribute_names=['permissions'])
                    
                await self._recalculate_and_apply_permissions(
                    role=role_to_update, 
                    direct_perm_names_override=update_data.permissions
                )
                
                # 3. [CRITICAL] Trigger cascade update for all children
                await self._cascade_update_children(role_to_update)

        # Re-fetch the updated role with its new permissions
        return await self.role_dao.get_by_pk(role_to_update.id)


    async def _recalculate_and_apply_permissions(
        self, 
        role: Role, 
        direct_perm_names_override: Optional[List[str]] = None
    ):
        """
        [Core Recalculation Logic] Recalculates a single role's full permission set and updates it.
        """
        if 'permissions' in attributes.instance_state(role).unloaded:
            await self.db.refresh(role, attribute_names=['permissions'])
        # 1. Get parent's full permissions
        parent_permissions = set()
        if role.parent_id:
            parent_role = await self.role_dao.get_one(where={"id": role.parent_id}, withs=["permissions"])
            if parent_role:
                parent_permissions = set(parent_role.permissions)

        # 2. Determine the direct permissions for this role
        direct_perm_names = set()
        if direct_perm_names_override is not None:
            # An update is happening, use the new direct permissions
            direct_perm_names = set(direct_perm_names_override)
        else:
            # This is a cascade, we need to figure out its original direct permissions.
            # This is the trickiest part. We find its permissions that are NOT in its parent.
            if role.parent_id:
                direct_perm_names = {p.name for p in role.permissions} - {p.name for p in parent_permissions}
            else:
                direct_perm_names = {p.name for p in role.permissions}
        
        direct_permission_objects = await self._resolve_permission_names_to_objects(direct_perm_names)
        
        # 3. Combine and apply
        final_permission_set = parent_permissions.union(set(direct_permission_objects))
        role.permissions = list(final_permission_set)
        self.db.add(role)

    async def _cascade_update_children(self, parent_role: Role):
        """Recursively recalculates permissions for all descendants of a role."""
        # A recursive CTE is the most efficient way to find all descendants.
        # For simplicity in this example, we'll do a simple (less efficient) breadth-first traversal.
        
        children_to_process = [parent_role]
        
        while children_to_process:
            current_parent = children_to_process.pop(0)
            
            # Load children for the current parent
            await self.db.refresh(current_parent, attribute_names=['children'])

            for child in current_parent.children:
                # The child's direct permissions haven't changed, so we pass None
                await self._recalculate_and_apply_permissions(child, direct_perm_names_override=None)
                children_to_process.append(child)

    async def delete_role(self, role: Role) -> None:
        # [SIMPLIFIED & SECURE]
        # Unified Rule: Any role that is not a custom team role cannot be deleted.
        # Rule 1: Absolutely cannot delete system plan roles.
        if role.role_type == RoleType.SYSTEM_PLAN:
            raise ServiceException("System Plan roles (e.g., 'plan:free') are fundamental and cannot be deleted.")

        # Rule 2 (Business Decision): We also protect system team templates from deletion.
        if role.role_type == RoleType.SYSTEM_TEAM_TEMPLATE:
            raise ServiceException("System Team Template roles (e.g., 'team:owner') cannot be deleted as they are required for new teams.")
            
        if role.role_type != RoleType.CUSTOM_TEAM:
            raise ServiceException(
                f"System roles of type '{role.role_type.value}' (e.g., '{role.name}') are fundamental "
                "and cannot be deleted. You can deactivate them if needed."
            )

        # The remaining checks for usage are still valid and necessary for CUSTOM roles.
        if await self.role_dao.get_one(where={"parent_id": role.id}):
            raise ServiceException(f"Cannot delete role '{role.name}' as other roles inherit from it.")
        
        if await self.db.scalar(select(func.count(TeamMember.id)).where(TeamMember.role_id == role.id)) > 0:
            raise ServiceException(f"Cannot delete role '{role.name}' as it is still assigned to team members.")

        await self.db.delete(role)
        await self.db.flush()