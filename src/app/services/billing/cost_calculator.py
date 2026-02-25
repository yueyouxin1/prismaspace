# src/app/services/billing/cost_calculator.py

from decimal import Decimal
from typing import NamedTuple, Optional, Dict, List, Any
from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.product.product_schemas import PriceInfo, TierInfo
from app.services.exceptions import ConfigurationError

class CalculatedCost(NamedTuple):
    """A standard data structure for cost calculation results."""
    cost: Decimal
    price_snapshot: Dict[str, Any] # For auditing, e.g., {'amount': 0.0015, 'unit_count': 1000, 'currency': 'USD', 'tiers': [...]}

class CostCalculator:
    """
    [CRITICAL NEW COMPONENT - System Layer] A stateless service for calculating the financial cost of a given usage.
    This is the single source of truth for all pricing logic and tier application.
    """
    def __init__(self): # [IMPROVEMENT] Inject AppContext
        pass

    async def calculate(
        self, 
        usage: Decimal, 
        price_info: PriceInfo,
        # user_id: Optional[int] = None, # [FUTURE] For user-specific discounts/tiers
        # team_id: Optional[int] = None  # [FUTURE] For team-specific contracts
    ) -> CalculatedCost:
        """
        [CORE LOGIC] Calculates the cost for a given feature usage based on resolved pricing.
        This method now correctly handles tiered pricing.
        """

        # 1. Perform the core calculation, handling tiered vs. flat pricing
        final_cost = Decimal('0.0')

        if price_info.tiers:
            # [CRITICAL CHANGE] Apply tiered pricing logic
            final_cost = self._calculate_tiered_cost(
                usage, 
                price_info.tiers,
                Decimal(price_info.unit_count)
            )
        elif price_info.amount is not None:
            # Flat rate pricing
            final_cost = (usage / Decimal(price_info.unit_count)) * price_info.amount
        else:
            # Should not happen due to PriceInfo's validator, but defensive programming
            raise ConfigurationError("Invalid PriceInfo: no flat amount or tiers defined.")

        # 2. [FUTURE] Apply business logic: e.g., user-specific discounts, promotional offers
        # (This would be injected here, after the base calculation)

        # 3. Prepare price snapshot for auditing
        price_snapshot = price_info.model_dump(mode='json') # Use model_dump for JSON serialization

        return CalculatedCost(cost=final_cost, price_snapshot=price_snapshot)

    def _calculate_tiered_cost(self, usage: Decimal, tiers: List[TierInfo], unit_count: Decimal) -> Decimal:
        """
        Calculates cost based on tiered pricing. Assumes tiers are sorted.
        The `amount` in each tier is the price per `unit_count` units.
        """
        if unit_count <= 0: # 防御性编程
            return Decimal('0.0')

        remaining_usage = usage
        total_cost = Decimal('0.0')
        previous_up_to = Decimal('0')

        sorted_tiers = sorted(tiers, key=lambda t: Decimal(t.up_to) if t.up_to is not None else Decimal('inf'))

        for tier in sorted_tiers:
            tier_up_to = Decimal(tier.up_to) if tier.up_to is not None else Decimal('inf')
            
            usage_in_tier = min(remaining_usage, tier_up_to - previous_up_to)
            
            if usage_in_tier <= 0:
                break
            
            # Tier amount is price per `unit_count` units
            cost_for_tier = (usage_in_tier / unit_count) * tier.amount
            total_cost += cost_for_tier
            
            remaining_usage -= usage_in_tier
            
            if remaining_usage <= 0:
                break

            previous_up_to = tier_up_to
            
        if remaining_usage > 0:
            if sorted_tiers:
                # Use the rate of the last (highest) tier for the remainder
                last_tier_rate = sorted_tiers[-1].amount
                total_cost += (remaining_usage / unit_count) * last_tier_rate
            else:
                # Should not happen if tiers list is not empty, but as a safeguard
                pass
        
        return total_cost