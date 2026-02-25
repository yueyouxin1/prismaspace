# src/app/dao/billing/entitlement_balance_dao.py

from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, and_
from decimal import Decimal
from app.dao.base_dao import BaseDao
from app.models import Feature, EntitlementBalance, EntitlementBalanceStatus, User, Team

class EntitlementBalanceDao(BaseDao[EntitlementBalance]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(EntitlementBalance, db_session)

    async def get_active_balance_for_feature(
        self, 
        owner: User | Team, 
        feature: Feature
    ) -> List[int]:
        """
        Finds all active EntitlementBalance IDs for a specific owner and feature,
        ordered by consumption priority (expiring soonest first).
        """
        owner_filter = (
            EntitlementBalance.owner_team_id == owner.id 
            if isinstance(owner, Team) 
            else EntitlementBalance.owner_user_id == owner.id
        )
        
        stmt = (
            select(EntitlementBalance)
            .where(
                owner_filter,
                EntitlementBalance.feature_id == feature.id,
                EntitlementBalance.status == EntitlementBalanceStatus.ACTIVE
            )
            .order_by(EntitlementBalance.end_date.asc().nulls_last(), EntitlementBalance.id.asc())
        )
        result = await self.db_session.execute(stmt)
        return result.scalars().all()

    async def get_active_balance_ids_for_feature(
        self, 
        owner: User | Team, 
        feature: Feature
    ) -> List[int]:
        """
        Finds all active EntitlementBalance IDs for a specific owner and feature,
        ordered by consumption priority (expiring soonest first).
        """
        owner_filter = (
            EntitlementBalance.owner_team_id == owner.id 
            if isinstance(owner, Team) 
            else EntitlementBalance.owner_user_id == owner.id
        )
        
        stmt = (
            select(EntitlementBalance.id)
            .where(
                owner_filter,
                EntitlementBalance.feature_id == feature.id,
                EntitlementBalance.status == EntitlementBalanceStatus.ACTIVE
            )
            .order_by(EntitlementBalance.end_date.asc().nulls_last(), EntitlementBalance.id.asc())
        )
        result = await self.db_session.execute(stmt)
        return result.scalars().all()

    async def get_all_active_balances_for_owner(
        self, 
        owner: User | Team
    ) -> List[EntitlementBalance]:
        """
        Fetches all active EntitlementBalance ORM objects for a specific owner.
        """
        owner_filter = (
            EntitlementBalance.owner_team_id == owner.id 
            if isinstance(owner, Team) 
            else EntitlementBalance.owner_user_id == owner.id
        )
        
        stmt = select(EntitlementBalance).where(owner_filter, EntitlementBalance.status == EntitlementBalanceStatus.ACTIVE)
        result = await self.db_session.execute(stmt)
        return result.scalars().all()

    async def expire_resettable_balances(self, owner: User | Team, source_entitlement_id: int):
        """
        [Hardened] Finds all active, resettable balances from a specific source and expires them.
        This prevents accidentally expiring one-time packages.
        """
        owner_filter = EntitlementBalance.owner_user_id == owner.id if isinstance(owner, User) else EntitlementBalance.owner_team_id == owner.id
        
        stmt = (
            update(EntitlementBalance)
            .where(
                owner_filter,
                EntitlementBalance.source_entitlement_id == source_entitlement_id,
                EntitlementBalance.status == EntitlementBalanceStatus.ACTIVE
            )
            .values(status=EntitlementBalanceStatus.EXPIRED)
        )
        await self.db_session.execute(stmt)