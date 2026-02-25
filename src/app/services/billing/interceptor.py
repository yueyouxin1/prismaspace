# app/services/billing/interceptor.py

import json
import logging
from decimal import Decimal
from datetime import timedelta
from typing import NamedTuple, List, Optional
from sqlalchemy.orm import selectinload
from sqlalchemy import select

from app.core.context import AppContext
from app.models import AccountStatus, Currency, Feature, EntitlementBalance, EntitlementBalanceStatus, User, Team
from app.dao.billing.entitlement_balance_dao import EntitlementBalanceDao
from app.services.exceptions import ServiceException
from app.services.billing.pricing_provider import PricingProvider
from app.services.billing.cost_calculator import CostCalculator
from app.schemas.product.product_schemas import PriceInfo
from .types.interceptor import ReservationResult

# 1. --- 定义清晰的、特定领域的异常 ---
class InsufficientFundsError(ServiceException):
    """Raised when the shadow ledger balance is insufficient for a reservation."""
    pass

class FeatureNotBillableError(ServiceException):
    """Raised when a feature is not configured for pay-as-you-go billing."""
    pass

class BillingConfigurationError(ServiceException):
    """Raised for underlying configuration issues (e.g., missing Lua script)."""
    pass
    
class BillingInterceptor:
    """
    A high-performance, robust gateway for atomically reserving funds from the shadow ledger in Redis.
    It orchestrates pricing, cost calculation, and atomic Redis operations via Lua scripting.
    """
    _LUA_SCRIPT_NAME = 'reserve_feature'
    _FEATURE_META_CACHE_TTL = timedelta(minutes=15)
    _LEDGER_LOCK_TIMEOUT = timedelta(seconds=5)
    _LEDGER_INIT_FLAG = "initialized"

    def __init__(self, context: AppContext, billing_entity: User | Team):
        self.app_context = context
        self.db = context.db
        self.redis = context.redis_service
        self.pricing_provider = PricingProvider(context.db, context.redis_service)
        self.cost_calculator = CostCalculator()
        self.entitlement_balance_dao = EntitlementBalanceDao(context.db)

        # [关键] 明确定义计费主体和账本 Key
        if not billing_entity:
            raise BillingConfigurationError("BillingInterceptor initialized without a valid billing entity (User or Team).")
        self.billing_entity = billing_entity
        self.account_status = self._check_billing_entity_status(self.billing_entity)
        self.billing_entity_id = self.billing_entity.id
        self.billing_entity_type = 'team' if isinstance(self.billing_entity, Team) else 'user'
        self.ledger_key = f"shadow_ledger:{self.billing_entity_type}:{self.billing_entity_id}"
    
    # --- 公共核心方法 ---
    
    async def reserve(self, feature: Feature, usage: Decimal, currency: Currency) -> ReservationResult:
        """
        The single, public entry point for reserving funds.
        It encapsulates the entire Estimate-Freeze-Settle (EFS) "Freeze" step.
        """
        if not feature:
            raise FeatureNotBillableError(f"Feature is required.")

        if usage <= 0:
            return ReservationResult(estimated_cost=Decimal(0), reserved_from_wallet=Decimal(0), reserved_from_entitlements={}, price_snapshot={})

        # 1. [健壮性] 确保影子账本已初始化 (冷启动保护)
        await self._ensure_ledger_initialized()

        # 2. 准备 Lua 脚本所需的元数据
         # --- 价格信息 ---
        price_info = await self.pricing_provider.get_price_info(feature, currency)
        
        # --- 权益包信息 ---
        # 查找所有能覆盖此 Feature 的、属于当前计费主体的、活跃的权益包
        entitlement_ids = await self._get_priority_entitlement_ids_for_feature(feature)

        # 准备价格参数
        tiers_json = "[]"  # 默认为空数组
        flat_amount = "0"
        unit_count = "1"

        if price_info:
            unit_count = str(price_info.unit_count)
            if price_info.tiers:
                # 如果是阶梯定价，序列化tiers列表
                # 我们只传递关键信息，减少数据量
                tier_data_for_lua = [
                    {"up_to": t.up_to, "amount": t.amount} for t in price_info.tiers
                ]
                tiers_json = json.dumps(tier_data_for_lua)
            elif price_info.amount is not None:
                # 如果是平面定价，传递amount
                flat_amount = str(price_info.amount)

        # 3. 执行原子性的 Lua 脚本
        try:
            lua_args = [
                str(usage),
                json.dumps(entitlement_ids),
                flat_amount,  # ARGV[3]
                unit_count,   # ARGV[4]
                tiers_json    # ARGV[5]
            ]
            # redis.execute_lua_script 应该被设计为能自动加载并使用SHA来执行脚本
            result = await self.redis.execute_lua_script(
                self._LUA_SCRIPT_NAME,
                keys=[self.ledger_key],
                args=lua_args
            )
        except Exception as e:
            logging.error(f"Failed to execute Lua script '{self._LUA_SCRIPT_NAME}' for ledger '{self.ledger_key}': {e}", exc_info=True)
            raise BillingConfigurationError(f"Failed to communicate with billing service: {e}")

        # 4. 解释脚本返回的结果
        return await self._interpret_lua_result(result, feature.name, price_info)

    # --- 内部辅助方法 ---

    def _check_billing_entity_status(self, billing_entity: User | Team) -> AccountStatus:
        if not hasattr(billing_entity, 'billing_account') or not billing_entity.billing_account:
            raise BillingConfigurationError("Billing entity is missing a loaded billing account.")
        account_status = billing_entity.billing_account.status

        # --- [第一道防线] ---
        if account_status != AccountStatus.ACTIVE:
            raise InsufficientFundsError(f"Account is not in ACTIVE state (current: {account_status.value}). Billing is suspended.")

        return account_status

    async def _get_priority_entitlement_ids_for_feature(self, feature: Feature) -> List[int]:
        """Queries the database for active entitlement balances for the current entity."""
        return await self.entitlement_balance_dao.get_active_balance_ids_for_feature(
            owner=self.billing_entity,
            feature=feature
        )

    async def _ensure_ledger_initialized(self):
        """
        Atomically ensures the shadow ledger HASH is initialized from the authoritative database.
        This uses a lock-and-double-check pattern to prevent race conditions during initialization.
        """
        # 快速路径: 检查初始化标志，如果已初始化，直接返回。
        if await self.redis.client.hget(self.ledger_key, self._LEDGER_INIT_FLAG) == "1":
            return
        
        # 慢速路径: 尝试获取分布式锁
        lock_key = f"{self.ledger_key}:lock"
        async with self.redis.client.lock(lock_key, timeout=self._LEDGER_LOCK_TIMEOUT.total_seconds()) as lock:
            # 双重检查: 在获得锁后，再次检查是否已被其他进程初始化
            if await self.redis.client.hget(self.ledger_key, self._LEDGER_INIT_FLAG) == "1":
                return
            
            logging.info(f"Initializing shadow ledger for '{self.ledger_key}'...")
            
            # --- 从数据库获取权威数据 ---
            # 1. 获取钱包余额
            # [健壮性关键] 在初始化影子账本这个关键时刻，我们必须确保我们拥有
            # 关于 billing_account 的最新数据，以防它在 AppContext 创建后被其他进程更新。
            await self.app_context.db.refresh(self.billing_entity, ['billing_account'])

            account_status = self._check_billing_entity_status(self.billing_entity)

            wallet_balance = self.billing_entity.billing_account.balance

            # 2. 获取所有活跃的权益余额
            active_entitlements = await self._get_all_active_entitlements()

            # --- 原子性地写入 Redis ---
            async with self.redis.client.pipeline(transaction=True) as pipe:
                # 先删除可能存在的旧数据（以防万一）
                pipe.delete(self.ledger_key)
                
                # 写入钱包余额和初始化标志
                ledger_data = {
                    'wallet_balance': str(wallet_balance),
                    self._LEDGER_INIT_FLAG: "1"
                }
                
                # 写入所有权益包的余额
                for ent in active_entitlements:
                    remaining_quota = ent.granted_quota - ent.consumed_usage
                    if remaining_quota > 0:
                        ledger_data[f"entitlement:{ent.id}"] = str(remaining_quota)
                
                pipe.hset(self.ledger_key, mapping=ledger_data)
                await pipe.execute()
            
            logging.info(f"Shadow ledger '{self.ledger_key}' initialized successfully.")

    async def _get_all_active_entitlements(self) -> List[EntitlementBalance]:
        """Fetches all active entitlements for the current billing entity."""
        return await self.entitlement_balance_dao.get_all_active_balances_for_owner(
            owner=self.billing_entity
        )

    async def _interpret_lua_result(self, result: List, feature_name: str, price_info: PriceInfo) -> ReservationResult:
        """Parses the structured result from the Lua script and raises appropriate exceptions."""
        if not isinstance(result, list) or len(result) == 0:
            raise BillingConfigurationError(f"Invalid response from Lua script: {result}")

        status_code = int(result[0])
        message = result[1] if len(result) > 1 else ""

        if status_code == 3: # Success
            reserved_wallet = Decimal(result[2])
            entitlement_details = result[3] if len(result) > 3 else "{}"
            
            try:
                # Lua cjson encodes JSON, so we need to decode it here
                reserved_entitlements = {int(k): Decimal(v) for k, v in json.loads(entitlement_details).items()}
            except (json.JSONDecodeError, TypeError):
                logging.error(f"Failed to parse entitlement details from Lua: {entitlement_details}")
                reserved_entitlements = {}

            # Calculate total estimated cost
            total_estimated_cost = reserved_wallet
            for usage in reserved_entitlements.values():
                 # We assume entitlements have the same unit value as pay-as-you-go for estimation
                 if price_info:
                     cost_result = await self.cost_calculator.calculate(usage, price_info)
                     total_estimated_cost += cost_result.cost
            
            return ReservationResult(
                estimated_cost=total_estimated_cost,
                reserved_from_wallet=reserved_wallet,
                reserved_from_entitlements=reserved_entitlements,
                price_snapshot=price_info.model_dump() if price_info else {}
            )
        elif status_code == 1:
            raise InsufficientFundsError(f"Insufficient wallet balance for feature '{feature_name}'. {message}")
        elif status_code == 2:
            raise InsufficientFundsError(f"Entitlements depleted for feature '{feature_name}'. {message}")
        elif status_code == 0:
            raise BillingConfigurationError(f"Billing configuration error for feature '{feature_name}'. {message}")
        else:
            raise ServiceException(f"Unknown error from billing service for feature '{feature_name}'. {message}")