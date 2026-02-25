# src/app/services/billing/entitlement_service.py

import logging
from decimal import Decimal
from typing import List, Union, Optional
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import AppContext
from app.dao.billing.entitlement_balance_dao import EntitlementBalanceDao
from app.dao.identity.team_dao import TeamDao
from app.models import (
    Product, Feature, User, Team, 
    EntitlementBalance, EntitlementBalanceStatus
)
from app.schemas.billing.entitlement_schemas import EntitlementBalanceRead
from app.services.base_service import BaseService
from app.services.exceptions import NotFoundError
from app.services.billing.types.interceptor import ReservationSnapshot

class EntitlementService(BaseService):
    """
    [领域服务] 权威地管理权益包的授予、消耗和生命周期。
    这是所有与EntitlementBalance相关的业务逻辑的唯一事实来源。
    """
    def __init__(self, context: AppContext):
        self.context = context
        self.db: AsyncSession = context.db
        self.dao = EntitlementBalanceDao(context.db)
        self.team_dao = TeamDao(context.db)
    
    # --------------------------------------------------------------------------
    # 公共核心方法 (Public Core Methods)
    # --------------------------------------------------------------------------

    async def consume_usage(
        self, 
        owner: Union[User, Team], 
        feature: Feature, 
        amount: Decimal,
        reservation_snapshot: Optional[ReservationSnapshot] = None
    ) -> Decimal:
        """
        [权威消耗入口] 尝试从权益包中消耗指定数量的用量。

        此方法实现了双模式执行：
        1.  如果提供了`reservation_snapshot`，则进入高优先级的“快照核实模式”，
            以解决异步竞态条件。
        2.  否则，回退到“实时查询模式”，用于同步或未预留的消耗场景。

        Args:
            owner: 权益的所有者 (User 或 Team)。
            feature: 被消耗的计费单元。
            amount: 要消耗的真实用量 (real usage)。
            reservation_snapshot: (可选) 来自BillingInterceptor的预留快照。

        Returns:
            一个Decimal值，表示未能被任何权益包覆盖的用量。
        """
        if amount <= 0:
            return Decimal('0')

        if reservation_snapshot and reservation_snapshot.reserved_from_entitlements:
            logging.info(f"[EntitlementService] Entering Snapshot Verification Mode for owner {owner.id}, amount {amount}.")
            return await self._consume_from_snapshot(owner, amount, reservation_snapshot)
        else:
            logging.info(f"[EntitlementService] Entering Live Query Mode for owner {owner.id}, feature '{feature.name}', amount {amount}.")
            return await self._consume_from_live_query(owner, feature, amount)

    async def grant_entitlements_for_product(
        self, 
        product: Product, 
        owner: Union[User, Team], 
        cycle_end_date: Optional[datetime]
    ):
        """
        根据产品模板，为所有者授予新的权益包。
        对于可重置的权益，此方法会先将旧的权益包设置为过期。
        """
        if not product.entitlements:
            logging.warning(f"Product '{product.name}' was granted but has no entitlements.")
            return

        grant_timestamp = datetime.utcnow()

        async with self.db.begin_nested():
            for entitlement_template in product.entitlements:
                if entitlement_template.is_resettable:
                    await self.dao.expire_resettable_balances(owner, entitlement_template.id)

                new_end_date = cycle_end_date
                if not entitlement_template.is_resettable and entitlement_template.validity_period_days:
                    new_end_date = grant_timestamp + timedelta(days=entitlement_template.validity_period_days)
                
                new_balance = EntitlementBalance(
                    source_entitlement_id=entitlement_template.id,
                    feature_id=entitlement_template.feature_id,
                    granted_quota=Decimal(entitlement_template.quota),
                    start_date=grant_timestamp,
                    end_date=new_end_date,
                    consumed_usage=Decimal(0),
                    status=EntitlementBalanceStatus.ACTIVE
                )
                if isinstance(owner, User): 
                    new_balance.owner_user_id = owner.id
                else: 
                    new_balance.owner_team_id = owner.id
                
                self.db.add(new_balance)

    async def list_balances_for_owner(self, owner: Union[User, Team]) -> List[EntitlementBalanceRead]:
        """列出指定所有者的所有有效权益包。"""
        owner_filter = {"owner_user_id": owner.id} if isinstance(owner, User) else {"owner_team_id": owner.id}
        
        balances = await self.dao.get_list(
            where={**owner_filter, "status": EntitlementBalanceStatus.ACTIVE},
            withs=[{"name": "source_entitlement", "withs": ["product", "feature"]}]
        )
        return [EntitlementBalanceRead.model_validate(b) for b in balances]
        
    async def list_balances_for_team(self, team_uuid: str, actor: User) -> List[EntitlementBalanceRead]:
        """安全地列出指定团队的所有有效权益包。"""
        team = await self.team_dao.get_by_uuid(team_uuid)
        if not team:
            raise NotFoundError("Team not found.")

        await self.context.perm_evaluator.ensure_can(["billing:read"], target=team)
        
        return await self.list_balances_for_owner(team)

    # --------------------------------------------------------------------------
    # 内部实现方法 (Internal Implementation Methods)
    # --------------------------------------------------------------------------

    async def _consume_from_snapshot(
        self,
        owner: Union[User, Team],
        total_amount_to_consume: Decimal,
        snapshot: ReservationSnapshot
    ) -> Decimal:
        """
        [内部] 快照核实模式：根据预留快照的“支付路径”进行消耗。
        """
        uncovered_usage = total_amount_to_consume
        reserved_from_entitlements = snapshot.reserved_from_entitlements
        
        async with self.db.begin_nested():
            entitlement_ids_to_check = [int(eid) for eid in reserved_from_entitlements.keys()]
            
            # 防御性编程：如果快照为空或用量已为0，则无需数据库操作
            if not entitlement_ids_to_check or uncovered_usage <= 0:
                return uncovered_usage

            # 一次性查询并用行级锁锁定所有相关的权益包
            stmt = select(EntitlementBalance).where(
                EntitlementBalance.id.in_(entitlement_ids_to_check)
            ).with_for_update()
            
            result = await self.db.execute(stmt)
            current_entitlements_map = {ent.id: ent for ent in result.scalars().all()}

            # 严格按照快照中记录的权益包ID顺序进行消耗
            for ent_id_str, _ in reserved_from_entitlements.items():
                if uncovered_usage <= 0: 
                    break

                ent_id = int(ent_id_str)
                current_entitlement = current_entitlements_map.get(ent_id)

                # 核实权益包在结算这一刻仍然是有效的
                if current_entitlement and current_entitlement.status == EntitlementBalanceStatus.ACTIVE:
                    available_in_entitlement = current_entitlement.granted_quota - current_entitlement.consumed_usage
                    
                    if available_in_entitlement > 0:
                        # 从这个包里消耗真实用量中尚未覆盖的部分
                        consume_from_this = min(uncovered_usage, available_in_entitlement)
                        
                        current_entitlement.consumed_usage += consume_from_this
                        if current_entitlement.consumed_usage >= current_entitlement.granted_quota:
                            current_entitlement.status = EntitlementBalanceStatus.DEPLETED
                        
                        uncovered_usage -= consume_from_this
                        logging.info(f"[EntitlementService-Snapshot] Consumed {consume_from_this} from entitlement {ent_id} for owner {owner.id}.")
                else:
                    # 如果预留时有效的包现在已失效（过期、被删除、耗尽），则跳过它。
                    # 这部分用量将保持 un-covered 状态，最终由钱包支付。
                    logging.warning(f"[EntitlementService-Snapshot] Snapshotted entitlement {ent_id} is now invalid or depleted for owner {owner.id}. Skipping.")
        
        return uncovered_usage

    async def _consume_from_live_query(
        self,
        owner: Union[User, Team], 
        feature: Feature, 
        amount: Decimal
    ) -> Decimal:
        """
        [内部] 实时查询模式：动态查询当前有效的权益包进行消耗。
        """
        remaining_to_consume = amount
        
        # DAO层按过期时间优先的顺序返回权益包
        active_entitlements = await self.dao.get_active_balance_for_feature(owner=owner, feature=feature)

        async with self.db.begin_nested():
            for entitlement in active_entitlements:
                if remaining_to_consume <= 0:
                    break
                
                # 为每个权益包加上行级锁，防止并发消耗
                await self.db.refresh(entitlement, with_for_update=True)

                available_in_entitlement = entitlement.granted_quota - entitlement.consumed_usage

                if available_in_entitlement > Decimal('0'):
                    consume_from_this = min(remaining_to_consume, available_in_entitlement)
                    
                    entitlement.consumed_usage += consume_from_this
                    if entitlement.consumed_usage >= entitlement.granted_quota:
                        entitlement.status = EntitlementBalanceStatus.DEPLETED
                    
                    remaining_to_consume -= consume_from_this
                    logging.info(f"[EntitlementService-LiveQuery] Consumed {consume_from_this} from entitlement {entitlement.id} for owner {owner.id}.")
        
        return remaining_to_consume