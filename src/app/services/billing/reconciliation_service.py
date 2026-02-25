# app/services/billing/reconciliation_service.py

import logging
from decimal import Decimal
from typing import List

from app.core.context import AppContext
from app.models import User, Team, EntitlementBalance, EntitlementBalanceStatus
from app.services.redis_service import RedisService
from .types.interceptor import ReservationResult
from .interceptor import BillingConfigurationError
from app.dao.billing.entitlement_balance_dao import EntitlementBalanceDao

class ReconciliationService:
    """
    [Production-Ready] A dedicated service for reconciling the shadow ledger in Redis
    with the authoritative database state. It performs atomic, incremental updates
    and supports full synchronization.
    """
    _LEDGER_INIT_FLAG = "initialized"

    def __init__(self, context: AppContext):
        self.context = context
        self.db = context.db # Used for fetching authoritative state
        self.redis: RedisService = context.redis_service
        self.entitlement_balance_dao = EntitlementBalanceDao(self.db)

    def _get_ledger_key(self, billing_entity: User | Team) -> str:
        """Centralized helper to generate the correct ledger key."""
        entity_type = 'team' if isinstance(billing_entity, Team) else 'user'
        return f"shadow_ledger:{entity_type}:{billing_entity.id}"

    # --- 增量校准方法 ---

    async def cancel_reservation(self, billing_entity: User | Team, reservation_result: ReservationResult):
        """
        [增量校准] 原子性地将一笔预留的资金退还到影子账本。
        """
        ledger_key = self._get_ledger_key(billing_entity)
        
        # 使用Redis Pipeline确保操作的原子性
        async with self.redis.client.pipeline(transaction=True) as pipe:
            # 1. 退还钱包预留
            if reservation_result.reserved_from_wallet > 0:
                pipe.hincrbyfloat(ledger_key, 'wallet_balance', float(reservation_result.reserved_from_wallet))

            # 2. 退还所有权益包预留
            for ent_id, usage in reservation_result.reserved_from_entitlements.items():
                if usage > 0:
                    pipe.hincrbyfloat(ledger_key, f"entitlement:{ent_id}", float(usage))
            
            try:
                await pipe.execute()
                logging.info(f"[Reconciliation] Successfully cancelled reservation on shadow ledger '{ledger_key}'.")
            except Exception as e:
                logging.critical(
                    f"CRITICAL: Failed to cancel reservation on shadow ledger '{ledger_key}'. "
                    f"Manual sync required. Error: {e}", exc_info=True
                )
                # 在这种关键失败场景下，我们应该抛出异常，让上层知道冲正失败
                raise BillingConfigurationError(f"Failed to cancel reservation: {e}")

    async def reconcile_wallet_balance(self, billing_entity: User | Team, delta: Decimal):
        """
        [增量校准] Atomically applies a delta to the shadow wallet balance.
        This is the primary method for "refunding" the difference between estimated and actual costs.
        """
        if delta == 0:
            return

        ledger_key = self._get_ledger_key(billing_entity)
        
        try:
            # HINCRBYFLOAT is atomic and safe for concurrent operations.
            new_balance = await self.redis.client.hincrbyfloat(ledger_key, 'wallet_balance', float(delta))
            
            log_action = "Refunded" if delta > 0 else "Applied extra charge of"
            logging.info(f"[Reconciliation] Wallet: {log_action} {abs(delta)} on shadow ledger '{ledger_key}'. "
                         f"New shadow balance: {new_balance:.8f}.")
        except Exception as e:
            # If reconciliation fails, it's critical to log it for manual intervention.
            # The authoritative ledger is correct, but the shadow ledger is now out of sync.
            logging.error(f"CRITICAL: Failed to reconcile shadow WALLET for '{ledger_key}' with delta {delta}. "
                          f"Manual sync required. Error: {e}", exc_info=True)
            # We should not re-raise the exception, as the authoritative settlement has already succeeded.
            # Logging is the main action here. Future improvements could involve a dead-letter queue for failed reconciliations.

    # --- 全量同步方法 ---

    async def _get_all_active_entitlements(self, billing_entity: User | Team) -> List[EntitlementBalance]:
        """Fetches all active entitlements for the current billing entity."""
        return await self.entitlement_balance_dao.get_all_active_balances_for_owner(
            owner=billing_entity
        )
        
    async def reconcile_all_entitlements(self, billing_entity: User | Team):
        """
        [全量同步] Fetches all authoritative entitlement balances from the DB and synchronizes
        them with the shadow ledger in Redis. This is a more comprehensive but heavier operation.
        """
        ledger_key = self._get_ledger_key(billing_entity)
        logging.info(f"[Reconciliation] Starting full entitlement sync for '{ledger_key}'...")
        
        try:
            # 1. --- 从数据库获取权威状态 ---
            authoritative_balances: List[EntitlementBalance] = await self._get_all_active_entitlements(
                billing_entity=billing_entity
            )
            
            authoritative_state = {}
            for ent in authoritative_balances:
                remaining_quota = ent.granted_quota - ent.consumed_usage
                # We only care about entitlements with a positive remaining balance
                if remaining_quota > 0:
                    authoritative_state[f"entitlement:{ent.id}"] = str(remaining_quota)
            
            # 2. --- 获取 Redis 中的影子状态 ---
            shadow_state = await self.redis.client.hgetall(ledger_key)
            
            # 3. --- 计算需要执行的 Redis 命令 ---
            commands_to_execute = {}
            
            # Find entitlements to update or add
            for field, value in authoritative_state.items():
                if shadow_state.get(field) != value:
                    commands_to_execute[field] = value
                    
            # Find entitlements to delete (they exist in shadow but not in authoritative)
            fields_to_delete = []
            for field in shadow_state.keys():
                if field.startswith("entitlement:") and field not in authoritative_state:
                    fields_to_delete.append(field)
            
            # 4. --- 在一个 Pipeline 中原子性地执行所有变更 ---
            if commands_to_execute or fields_to_delete:
                async with self.redis.client.pipeline(transaction=True) as pipe:
                    if commands_to_execute:
                        pipe.hset(ledger_key, mapping=commands_to_execute)
                    if fields_to_delete:
                        pipe.hdel(ledger_key, *fields_to_delete)
                    await pipe.execute()
                logging.info(f"[Reconciliation] Entitlement sync for '{ledger_key}' completed. "
                             f"Updated/Added: {len(commands_to_execute)}, Deleted: {len(fields_to_delete)}.")
            else:
                logging.info(f"[Reconciliation] Entitlement sync for '{ledger_key}': No changes needed.")

        except Exception as e:
            logging.error(f"CRITICAL: Failed to perform full ENTITLEMENT sync for '{ledger_key}'. "
                          f"Manual sync required. Error: {e}", exc_info=True)


    async def full_ledger_reconciliation(self, billing_entity: User | Team):
        """
        [终极审计] A top-level method to perform a full reconciliation of both
        wallet and entitlements. This can be called periodically by a maintenance task.
        """
        logging.info(f"[Reconciliation] Starting FULL ledger reconciliation for entity {billing_entity.id}...")
        
        # We can reuse the `_ensure_ledger_initialized` logic, but without the lock,
        # as this is intended to be a forceful overwrite.
        
        ledger_key = self._get_ledger_key(billing_entity)
        
        try:
            # 1. Fetch wallet balance
            await self.context.db.refresh(billing_entity, ['billing_account'])
            if not billing_entity.billing_account:
                raise BillingConfigurationError(f"Billing entity has no billing account for full reconciliation.")
            wallet_balance = billing_entity.billing_account.balance

            # 2. Fetch all active entitlements
            active_entitlements = await self._get_all_active_entitlements(billing_entity=billing_entity)

            # 3. Build the complete authoritative state
            authoritative_ledger_data = {
                'wallet_balance': str(wallet_balance),
                self._LEDGER_INIT_FLAG: "1"
            }
            for ent in active_entitlements:
                remaining_quota = ent.granted_quota - ent.consumed_usage
                if remaining_quota > 0:
                    authoritative_ledger_data[f"entitlement:{ent.id}"] = str(remaining_quota)
            
            # 4. Atomically overwrite the entire HASH
            # This is a safe way to do a full sync.
            async with self.redis.client.pipeline(transaction=True) as pipe:
                pipe.delete(ledger_key)
                pipe.hset(ledger_key, mapping=authoritative_ledger_data)
                await pipe.execute()

            logging.info(f"[Reconciliation] FULL ledger sync for '{ledger_key}' completed successfully.")
            
        except Exception as e:
             logging.error(f"CRITICAL: Failed to perform FULL ledger sync for '{ledger_key}'. "
                          f"Manual sync required. Error: {e}", exc_info=True)