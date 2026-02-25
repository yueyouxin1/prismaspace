# tests/api/v1/test_billing.py

import pytest
from httpx import AsyncClient
from typing import Callable, Dict, Any
from decimal import Decimal
from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import AsyncMock
from arq.connections import ArqRedis
from arq.worker import Worker
from tests.conftest import UserContext, registered_user_factory, wait_for_job_completion
from app.models import PlanTier, Resource, Feature, EntitlementBalance
from app.dao.product.feature_dao import FeatureDao
from app.dao.billing.entitlement_balance_dao import EntitlementBalanceDao
from app.services.redis_service import RedisService
from app.services.billing.reconciliation_service import ReconciliationService

pytestmark = pytest.mark.asyncio

# ==============================================================================
# 1. 核心 Fixtures (重构以体现角色和计费状态)
# ==============================================================================

@pytest.fixture
async def billable_feature(db_session: AsyncSession) -> Feature:
    """获取一个在 seed_data 中定义好的、可计费的 Feature。"""
    feature = await FeatureDao(db_session).get_one(where={"name": "limit:tool:custom:execution"})
    assert feature is not None, "Billable feature 'limit:tool:custom:execution' not found. Ensure DB is seeded."
    return feature

@pytest.fixture
async def pro_user_owner(
    db_session: AsyncSession,
    get_billable_cost: Callable,
    app_context_factory: Callable,
    registered_user_with_pro: UserContext
) -> UserContext:
    """
    [核心修正] 提供一个资源所有者:
    - 拥有 PRO 套餐 (自带1次免费 tool execution 额度)。
    - 拥有足够的钱包余额，用于额度用尽后的测试。
    - 确保其权益已同步到 Redis 影子账本。
    """
    billable_cost = await get_billable_cost(feature_name="limit:tool:custom:execution", usage="1")
    initial_balance = billable_cost * Decimal('10')
    registered_user_with_pro.user.billing_account.balance = initial_balance
    await db_session.flush()
    await db_session.refresh(registered_user_with_pro.user, ['billing_account'])

    # [关键] 模拟真实世界：在测试前，确保用户的权益已从DB同步到Redis
    app_context = await app_context_factory(registered_user_with_pro.user)
    reconciliation_service = ReconciliationService(app_context)
    await reconciliation_service.full_ledger_reconciliation(registered_user_with_pro.user)

    return registered_user_with_pro

@pytest.fixture
async def free_user_executor(registered_user_factory: Callable) -> UserContext:
    """提供一个执行者，免费套餐，零余额。"""
    return await registered_user_factory(initial_balance=Decimal('0'))

@pytest.fixture
async def billable_tool_instance(
    client: AsyncClient,
    auth_headers_factory: Callable,
    pro_user_owner: UserContext, # <--- 由 PRO 用户创建
    created_resource_factory: Callable,
    billable_feature: Feature,
    db_session: AsyncSession
) -> Resource:
    """
    [健壮版 Fixture]
    1. 由 `pro_user_owner` 创建一个 Tool 资源。
    2. 为其配置一个真实可执行的 URL 和 Schema。
    3. 将其可见性设置为 'public'，以便其他用户可以执行。
    """
    tool_resource = await created_resource_factory("tool")
    await db_session.refresh(tool_resource.workspace_instance)
    assert tool_resource.workspace_instance.linked_feature_id == billable_feature.id

    headers = await auth_headers_factory(pro_user_owner)
    instance_uuid = tool_resource.workspace_instance.uuid
    tool_config = {
        "url": "https://wttr.in/{city}",
        "method": "GET",
        "inputs_schema": [
            {
                "name": "city", "type": "string", "required": True, "open": True,
                "label": "City Name", "description": "The name of the city to get the weather for.",
                "role": "http.path"
            },
            {
                "name": "format", "type": "string", "required": False, "open": False,
                "role": "http.query", "value": {"type": "literal", "content": "j1"}
            }
        ],
        "outputs_schema": [
            {
                "name": "current_condition", "type": "array", "required": True,
                "items": {
                    "type": "object",
                    "properties": [
                        {"name": "temp_C", "type": "string", "required": True},
                        {"name": "humidity", "type": "string", "required": True},
                    ]
                }
            }
        ],
        "visibility": "public"
    }
    response = await client.put(f"/api/v1/instances/{instance_uuid}", json=tool_config, headers=headers)
    assert response.status_code == status.HTTP_200_OK

    return tool_resource

@pytest.fixture
async def assert_owner_is_billed_by_wallet(
    db_session: AsyncSession,
    real_redis_service: RedisService,
    arq_pool_mock: AsyncMock
) -> Callable:
    """断言帮助函数，验证所有者的 **钱包** 被扣款。"""
    async def _factory(
        owner_context: UserContext,
        executor_context: UserContext,
        initial_owner_balance: Decimal,
        billable_tool_cost: Decimal,
    ):
        # 刷新 ORM 对象以获取最新的 DB 状态
        await db_session.refresh(owner_context.user, attribute_names=['billing_account'])
        owner_id = owner_context.user.id
        executor_user_uuid = executor_context.user.uuid
        
        ledger_key = f"shadow_ledger:user:{owner_id}"
        final_balance_str = await real_redis_service.client.hget(ledger_key, "wallet_balance")
        assert final_balance_str is not None, "Owner's shadow ledger was not created."
        
        expected_balance = initial_owner_balance - billable_tool_cost
        # 验证权威钱包被扣除一次执行成本
        assert owner_context.user.billing_account.balance == expected_balance
        assert Decimal(final_balance_str) == pytest.approx(expected_balance)

        # 1. 确保它至少被调用了一次
        assert arq_pool_mock.enqueue_job.await_count >= 1, "enqueue_job was not called."
        
        # 2. 获取最后一次调用的参数
        last_call_args, last_call_kwargs = arq_pool_mock.enqueue_job.await_args
        
        # 3. 对参数进行精确断言
        assert last_call_args[0] == 'process_consumption_task'
        # last_call_args[1] 是 record.id，我们可以断言它是一个整数
        assert isinstance(last_call_args[1], int)
        # last_call_args[2] 应该是 executor 的 uuid
        assert last_call_args[2] == executor_context.user.uuid
    return _factory

# ==============================================================================
# 2. 核心测试套件
# ==============================================================================

class TestEntitlementPriority:
    """测试计费优先级：权益（Entitlement）优先于钱包（Wallet）。"""

    @pytest.fixture
    def execution_payload(self) -> Dict[str, Any]:
        return {"inputs": {"city": "London"}}

    async def test_entitlement_is_consumed_before_wallet(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        pro_user_owner: UserContext,
        free_user_executor: UserContext,
        billable_tool_instance: Resource,
        billable_feature: Feature,
        execution_payload: Dict,
        get_billable_cost: Callable,
        assert_owner_is_billed_by_wallet: Callable,
        arq_pool_mock: AsyncMock,
        real_arq_pool: ArqRedis,
        real_redis_service: RedisService,
        db_session: AsyncSession,
        arq_worker_for_test: Worker,
    ):
        """
        验证 PRO 用户的权益配额被优先消耗，额度用尽后才消耗钱包。
        """
        # --- Arrange ---
        # 创建 worker 需要的前提数据
        
        owner_id = pro_user_owner.user.id
        initial_owner_balance = pro_user_owner.user.billing_account.balance
        billable_cost = await get_billable_cost(feature_name="limit:tool:custom:execution", usage="1")
        entitlement = await EntitlementBalanceDao(db_session).get_one(
            where={"owner_user_id": owner_id, "feature_id": billable_feature.id}
        )
        assert entitlement is not None
        entitlement_key_in_redis = f"entitlement:{entitlement.id}"
        ledger_key = f"shadow_ledger:user:{owner_id}"
        executor_headers = await auth_headers_factory(free_user_executor)
        workspace_instance_uuid = billable_tool_instance.workspace_instance.uuid

        # ==========================================================
        # === 执行 1: 应该消耗权益 (Entitlement) ===
        # ==========================================================
        
        # --- Act 1 ---
        await db_session.commit()
        response1 = await client.post(
            f"/api/v1/execute/instances/{workspace_instance_uuid}",
            json=execution_payload, headers=executor_headers
        )
        await db_session.commit()
        assert response1.status_code == status.HTTP_200_OK

        # --- Sync for Worker ---
        # [关键步骤 B] 提交由 API 调用创建的 ConsumptionRecord
        #await db_session.commit()
        print("\n--- [E2E] ConsumptionRecord for run 1 committed. ---")

        # --- Assert 1 ---
        arq_pool_mock.enqueue_job.assert_called_once()
        job_id = arq_pool_mock.captured_job_result.job_id
        await wait_for_job_completion(real_arq_pool, job_id)
        
        # 断言 Redis 状态
        shadow_ledger_after_1 = await real_redis_service.client.hgetall(ledger_key)
        assert shadow_ledger_after_1.get(entitlement_key_in_redis) is None
        assert Decimal(shadow_ledger_after_1.get('wallet_balance')) == pytest.approx(initial_owner_balance)

        # ==========================================================
        # === 执行 2: 应该消耗钱包 (Wallet Fallback) ===
        # ==========================================================
        
        # --- Arrange 2 ---
        arq_pool_mock.reset_mock()
        if hasattr(arq_pool_mock, 'captured_job_result'):
            del arq_pool_mock.captured_job_result

        # --- Act 2 ---
        response2 = await client.post(
            f"/api/v1/execute/instances/{workspace_instance_uuid}",
            json=execution_payload, headers=executor_headers
        )
        await db_session.commit()
        assert response2.status_code == status.HTTP_200_OK

        # --- Sync for Worker ---
        # [关键步骤 C] 提交第二个 ConsumptionRecord
        #await db_session.commit()
        print("--- [E2E] ConsumptionRecord for run 2 committed. ---")

        # --- Assert 2 ---
        arq_pool_mock.enqueue_job.assert_called_once()
        job_id_2 = arq_pool_mock.captured_job_result.job_id
        await wait_for_job_completion(real_arq_pool, job_id_2)

        # 再次检查 Redis，确认权益字段仍然不存在
        shadow_ledger_after_2 = await real_redis_service.client.hgetall(ledger_key)
        assert shadow_ledger_after_2.get(entitlement_key_in_redis) is None
        # 断言钱包扣款
        await assert_owner_is_billed_by_wallet(
            owner_context=pro_user_owner,
            executor_context=free_user_executor,
            initial_owner_balance=initial_owner_balance,
            billable_tool_cost=billable_cost
        )