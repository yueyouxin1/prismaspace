# app/services/billing/consumption_service.py

import logging
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.context import AppContext
from app.models import (
    ConsumptionRecord, ConsumptionRecordStatus, BillingAccount, BillingTransaction, 
    TransactionType, TransactionStatus, User, Team
)
from app.dao.billing.consumption_record_dao import ConsumptionRecordDao
from app.services.exceptions import ServiceException, NotFoundError
from .interceptor import BillingConfigurationError
from .types.interceptor import ReservationSnapshot
from .reconciliation_service import ReconciliationService
from .entitlement_service import EntitlementService
from .pricing_provider import PricingProvider
from .cost_calculator import CostCalculator

class ConsumptionService:
    """
    [Production-Ready] Orchestrates the authoritative settlement of a usage ConsumptionRecord.
    It is responsible for:
    1.  Atomically and idempotently processing a ConsumptionRecord.
    2.  Applying free entitlements first on the authoritative database.
    3.  Calculating and debiting any overage cost from the authoritative wallet balance.
    4.  Triggering the reconciliation of the shadow ledger in Redis after a successful settlement.
    """
    def __init__(self, context: AppContext):
        self.context = context
        self.db: AsyncSession = context.db
        self.consumption_record_dao = ConsumptionRecordDao(self.db)
        # --- 核心依赖 ---
        self.entitlement_service = EntitlementService(context)
        self.pricing_provider = PricingProvider(context.db, context.redis_service)
        self.cost_calculator = CostCalculator() # Pure calculator
        self.reconciliation_service = ReconciliationService(context)

    async def process_record(self, record_id: int):
        # We will determine this inside the transaction
        actual_cost_from_wallet = Decimal('0')
        billing_owner: Optional[User | Team] = None

        async with self.db.begin_nested():
            # 1. --- 幂等性检查与行级锁定 ---
            record = await self.db.get(
                ConsumptionRecord, record_id, 
                options=[
                    selectinload(ConsumptionRecord.feature),
                    selectinload(ConsumptionRecord.billing_account).options(
                        selectinload(BillingAccount.user),
                        selectinload(BillingAccount.team)
                    )
                ],
                with_for_update=True
            )
            if not record:
                logging.error(f"[Consumption] ConsumptionRecord ID {record_id} not found. Task will be dropped.")
                return

            if record.status != ConsumptionRecordStatus.PENDING:
                logging.warning(f"[Consumption] ConsumptionRecord ID {record.id} is already in status '{record.status.value}'. Skipping.")
                return
            
            try:
                # 2. --- 加载权威数据并锁定账户 ---
                billing_account = record.billing_account
                if not billing_account:
                    raise NotFoundError(f"Billing account {record.billing_account_id} not found for record {record.id}.")
                
                billing_owner = billing_account.owner
                if not billing_owner:
                    raise NotFoundError(f"Billing owner not found for billing account {billing_account.id}.")

                # 检查并更新账户状态
                if billing_account.balance < 0 and billing_account.status == AccountStatus.ACTIVE:
                    billing_account.status = AccountStatus.DELINQUENT
                    logging.warning(f"Billing account {billing_account.id} has become DELINQUENT. New balance: {billing_account.balance}")
                    # [未来] 在这里可以触发发送“欠费通知”的事件

                # 3. --- 权威结算核心逻辑 ---
                # 解析预留快照
                snapshot = None
                if record.reservation_snapshot:
                    try:
                        snapshot = ReservationSnapshot.model_validate(record.reservation_snapshot)
                    except Exception as e:
                        logging.error(f"Failed to parse reservation snapshot for ConsumptionRecord {record.id}: {e}", exc_info=True)

                # 3a. 在权威账本上，消耗权益额度
                uncovered_usage = await self.entitlement_service.consume_usage(
                    owner=billing_owner,
                    feature=record.feature,
                    amount=record.usage,
                    reservation_snapshot=snapshot
                )
                logging.info(f"[Consumption] Authoritative Entitlement: ConsumptionRecord {record.id} ({record.usage} units). "
                             f"Covered: {record.usage - uncovered_usage}. Uncovered: {uncovered_usage}.")

                # 3b. 如果有未覆盖的用量，计算其真实货币成本
                actual_cost_from_wallet = Decimal('0')
                if uncovered_usage > 0:
                    price_info = await self.pricing_provider.get_price_info(record.feature, billing_account.currency)
                    if not price_info:
                        raise BillingConfigurationError(f"Cannot settle record {record.id}: No active price found for feature '{record.feature.name}' in currency '{billing_account.currency.value}'.")
                    
                    cost_result = await self.cost_calculator.calculate(uncovered_usage, price_info)
                    actual_cost_from_wallet = cost_result.cost
                
                # 3c. 如果需要从钱包扣款，则执行
                if actual_cost_from_wallet > 0:
                    billing_account.balance -= actual_cost_from_wallet
                    
                    transaction = BillingTransaction(
                        billing_account_id=billing_account.id,
                        amount=actual_cost_from_wallet,
                        type=TransactionType.DEBIT,
                        status=TransactionStatus.COMPLETED,
                        description=f"Usage charge for {record.feature.label}",
                        source_record_id=record.id,
                        source_product_id=record.feature.product.id if record.feature.product else None
                    )
                    self.db.add(transaction)
                    logging.info(f"[Consumption] Authoritative Wallet: Debited {actual_cost_from_wallet} {billing_account.currency.value} from account {billing_account.id} for record {record.id}.")

                # 4. --- 更新 ConsumptionRecord 记录，完成闭环 ---
                # `cost` 字段权威地记录了本次操作从钱包中扣除的金额。
                record.cost = actual_cost_from_wallet 
                record.status = ConsumptionRecordStatus.COMPLETED
                
            except Exception as e:
                # 5. --- 异常处理 ---
                logging.error(f"[Consumption] CRITICAL: Failed to process ConsumptionRecord ID {record.id}: {e}", exc_info=True)
                if record: # Ensure record was loaded before trying to update it
                    record.status = ConsumptionRecordStatus.FAILED
                    record.error_message = f"Settlement Error: {str(e)[:1000]}"
                raise
                # The 'async with' block will automatically roll back the savepoint.
                # The outer transaction will commit the status update to FAILED.
                
        # 6. --- 影子账本校准 (事务成功提交后执行) ---
        if record and record.status == ConsumptionRecordStatus.COMPLETED:
            await self._reconcile_shadow_ledger(record, billing_owner, actual_cost_from_wallet)

    async def _reconcile_shadow_ledger(self, record: ConsumptionRecord, billing_owner: User | Team, actual_cost_from_wallet: Decimal):
        if not billing_owner: return

        reservation = record.reservation_snapshot
        if reservation:
            reserved_from_wallet = Decimal(reservation.get("reserved_from_wallet", "0"))
            delta_to_refund_to_wallet = reserved_from_wallet - actual_cost_from_wallet
            if delta_to_refund_to_wallet != 0:
                await self.reconciliation_service.reconcile_wallet_balance(
                    billing_owner,
                    delta_to_refund_to_wallet
                )
        await self.reconciliation_service.reconcile_all_entitlements(billing_owner)