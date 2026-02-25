# src/app/services/identity/membership_service.py

from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from datetime import datetime
from app.core.context import AppContext
from app.services.base_service import BaseService
from app.models import User, Product, ProductType, Membership, MembershipStatus, MembershipHistory, MembershipChangeType
from app.dao.identity.membership_dao import MembershipDao, MembershipHistoryDao
from app.dao.permission.role_dao import RoleDao
from app.services.billing.entitlement_service import EntitlementService
from app.services.exceptions import ServiceException, ConfigurationError

class MembershipService(BaseService):
    """
    [Service Layer] Manages the lifecycle of a user's core identity contract.
    """
    def __init__(self, context: AppContext):
        self.context = context
        self.db = context.db
        self.dao = MembershipDao(context.db)
        self.history_dao = MembershipHistoryDao(context.db)
        self.role_dao = RoleDao(context.db)
        self.entitlement_service = EntitlementService(context)

    async def grant_membership_from_product(
        self,
        user: User,
        new_product: Product,
        cycle_start: datetime,
        cycle_end: Optional[datetime],
        change_type: MembershipChangeType = MembershipChangeType.GRANT # Default to GRANT
    ):
        """
        Atomically grants or updates a user's membership based on a purchased product.
        This is the single source of truth for changing a user's core plan.
        """
        if new_product.type != ProductType.MEMBERSHIP:
            raise ConfigurationError(f"Product '{new_product.name}' is not a MEMBERSHIP type product.")
        if not new_product.plan_tier or not new_product.granted_role_id:
            raise ConfigurationError(f"Membership product '{new_product.name}' is missing 'plan_tier' or 'granted_role_id'.")

        # 确认授权角色存在
        granted_role = await self.role_dao.get_by_pk(new_product.granted_role_id)
        if not granted_role:
            raise ConfigurationError(f"granted role not configured.")

        async with self.db.begin_nested():
            # 1. Find existing membership or create a new one
            current_membership = await self.dao.get_one(
                where={"user_id": user.id},
                withs=[{"name": "product", "withs": ["entitlements"]}]
            )

            old_product = current_membership.product if current_membership else None

            if current_membership:
                # Log the "before" state to the history table
                history_log = MembershipHistory(
                    user_id=user.id,
                    product_id=current_membership.product_id,
                    plan_tier=current_membership.plan_tier,
                    role_id=current_membership.role_id,
                    status=current_membership.status,
                    billing_cycle=current_membership.billing_cycle,
                    period_start=current_membership.current_period_start,
                    period_end=current_membership.current_period_end,
                    change_type=change_type, # e.g., UPGRADE, RENEW
                    notes=f"Changed to product '{new_product.name}'"
                )
                self.db.add(history_log)
                
                # Update the current record in-place
                membership_to_update = current_membership
            else:
                # This is the first time membership is granted
                membership_to_update = Membership(user_id=user.id)
                self.db.add(membership_to_update)
            
            # 2. Apply the new state from the product (Snapshotting)
            membership_to_update.product_id = new_product.id
            membership_to_update.plan_tier = new_product.plan_tier
            membership_to_update.role_id = new_product.granted_role_id
            membership_to_update.status = MembershipStatus.ACTIVE
            membership_to_update.billing_cycle = new_product.prices[0].billing_cycle if new_product.prices else BillingCycle.MONTH # Fallback
            membership_to_update.current_period_start = cycle_start
            membership_to_update.current_period_end = cycle_end

            if change_type == MembershipChangeType.RENEW:
                # For RENEWALS, we explicitly revoke the old product's resettable entitlements.
                if old_product:
                    await self.entitlement_service.revoke_resettable_entitlements_for_product(
                        product=old_product,
                        owner=user
                    )

            # 3. Orchestrate call to EntitlementService
            # For GRANT, UPGRADE, DOWNGRADE, we DO NOT revoke old entitlements.
            # This explicitly implements the "Stacking Model".
            
            # Grant entitlements for the new product, regardless of the change type.
            await self.entitlement_service.grant_entitlements_for_product(
                product=new_product,
                owner=user,
                cycle_end_date=cycle_end
            )

        # 4. After transaction succeeds, invalidate caches
        if self.context.auth:
            await self.context.perm_evaluator.invalidate_cache_for_user(user)

    async def get_current_membership(self, user: User) -> Optional[Membership]:
        """Retrieves the current active membership for a user."""
        return await self.dao.get_one(
            where={"user_id": user.id},
            withs=["product", "role"] # Eager load relations
        )