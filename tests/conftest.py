# tests/conftest.py

import asyncio
from typing import Optional, Dict, Any, List, AsyncGenerator, Callable
import pytest
import pathlib
import uuid
import time
from decimal import Decimal
from dataclasses import dataclass
from datetime import datetime, timedelta
from httpx import AsyncClient, ASGITransport
from fastapi import status
from sqlalchemy_utils import database_exists, create_database, drop_database
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
    AsyncConnection
)
from sqlalchemy.orm.exc import ObjectDeletedError
from sqlalchemy.orm import attributes, RelationshipProperty
from sqlalchemy.pool import NullPool
from contextlib import asynccontextmanager
from alembic.config import Config
from alembic import command
import redis.asyncio as aioredis
from unittest.mock import ANY, patch, AsyncMock, MagicMock
from arq.worker import Worker
from arq.jobs import Job, JobResult
from arq.connections import ArqRedis
from scripts.seed_initial_data import seed_all_data
from app.main import app
from app.core.context import AppContext
from app.api.dependencies.authentication import get_base_auth_context
from app.api.dependencies.authentication import get_auth, AuthContext
from app.services.permission.hierarchy import preload_permission_hierarchy
from app.services.permission.permission_evaluator import PermissionEvaluator
from app.db.session import get_db
from app.services.redis_service import RedisService
from app.services.billing.pricing_provider import PricingProvider
from app.engine.vector.base import VectorEngineService
from app.engine.vector.main import VectorEngineManager
from app.db.base import Base
from app.core.config import settings
from app.dao.identity.user_dao import UserDao
from app.dao.identity.team_dao import TeamDao
from app.dao.billing.billing_account_dao import BillingAccountDao
from app.dao.workspace.workspace_dao import WorkspaceDao
from app.dao.project.project_dao import ProjectDao
from app.dao.resource.resource_dao import ResourceDao
from app.dao.product.product_dao import ProductDao
from app.dao.product.feature_dao import FeatureDao
from app.dao.module.service_module_dao import ServiceModuleProviderDao
# [修复] 导入必要的模型
from app.models import User, Team, Workspace, Project, Resource, ResourceInstance, Product, ProductType, PlanTier, Membership, Currency
from app.dao.identity.membership_dao import MembershipDao
from app.dao.resource.resource_dao import ResourceInstanceDao

# 使用 NullPool 确保每个连接都是全新的，避免在异步测试中共享状态
test_engine = create_async_engine(
    settings.DATABASE_URL_TEST,
    poolclass=NullPool,
)

TestSessionLocal = async_sessionmaker(
    autocommit=False, autoflush=False, expire_on_commit=False, bind=test_engine, class_=AsyncSession
)


# ==============================================================================
# 1. 数据库和 Seeding Fixtures
# ==============================================================================

@pytest.fixture(scope="session")
def event_loop():
    """为整个测试会话创建一个事件循环。"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="session", autouse=True)
async def setup_database():
    """
    [重构后]
    管理整个测试会話的数据库生命周期。
    它现在使用 Alembic 来创建表结构，并调用通用的 seeding 脚本。
    """
    db_url = settings.DATABASE_URL_TEST
    # SQLAlchemy-utils 需要一个同步的 DSN
    sync_db_url = db_url.replace("+asyncpg", "")
    
    # 为确保每次测试运行都绝对干净，先删除再创建
    if database_exists(sync_db_url):
        print("\n--- Test DB found, dropping and recreating for a clean state. ---")
        try:
            drop_database(sync_db_url)
        except SQLAlchemyError as exc:
            if settings.DB_TEST_NAME not in str(exc):
                raise
            print(f"--- Test DB drop skipped due to race/no-op: {exc} ---")
    
    print(f"\n--- Creating test database: {settings.DB_TEST_NAME} ---")
    create_database(sync_db_url)

    # [关键修改] 使用 Alembic 来设置测试数据库的 schema
    print("--- Applying Alembic migrations to test DB... ---")
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", sync_db_url) # 指向测试数据库
    command.upgrade(alembic_cfg, "head")

    # [关键修改] 调用外部脚本进行数据填充
    seeding_engine = create_async_engine(db_url)
    SeedingSessionLocal = async_sessionmaker(bind=seeding_engine, class_=AsyncSession)
    
    async with SeedingSessionLocal() as db:
        async with db.begin():
            await seed_all_data(db)

    await seeding_engine.dispose()

    # yield 关键字将控制权交还给 pytest，以运行所有测试
    yield
    
    # 所有测试结束后，销毁数据库
    print(f"\n--- Dropping test database: {settings.DB_TEST_NAME} ---")
    drop_database(sync_db_url)

@pytest.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    [最终版 - E2E 模式] 使用连接级外层事务 + savepoint。
    这样测试代码和运行时派生会话都可以安全 commit，
    同时在测试结束时通过回滚外层事务实现完整清理。
    """
    async with test_engine.connect() as connection:
        outer_transaction = await connection.begin()
        RuntimeSessionLocal = async_sessionmaker(
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
            bind=connection,
            class_=AsyncSession,
            join_transaction_mode="create_savepoint",
        )
        async with RuntimeSessionLocal() as session:
            try:
                yield session
            finally:
                await session.close()
                if outer_transaction.is_active:
                    await outer_transaction.rollback()

# ==============================================================================
# 2. Mock 核心服务/引擎 Fixtures
# ==============================================================================

@pytest.fixture(scope="function")
def vector_manager_mock() -> AsyncMock:
    """
    [MODIFIED] 创建一个 VectorEngineManager 的 mock。
    它的 get_engine 方法会返回另一个 mock，以便我们断言其上的方法调用。
    """
    manager_mock = AsyncMock(spec=VectorEngineManager)
    engine_mock = AsyncMock(spec=VectorEngineService)
    manager_mock.get_engine.return_value = engine_mock
    return manager_mock

@pytest.fixture(scope="function")
async def real_redis_service() -> AsyncGenerator[RedisService, None]:
    """
    创建一个连接到真实测试 Redis 数据库的 RedisService 实例。
    在每个测试函数结束后，它会清空测试 Redis 数据库以确保隔离性。
    """
    # 1. 创建连接到测试数据库的 Redis 客户端
    test_redis_client = aioredis.from_url(
        settings.REDIS_URL_TEST, 
        encoding="utf-8", 
        decode_responses=True
    )
    assert hasattr(test_redis_client, 'aclose'), "The created redis client in fixture is missing 'aclose'!"
    # 2. 实例化 RedisService 并进行初始化（加载 Lua 脚本）
    redis_service = RedisService(client=test_redis_client)
    await redis_service.initialize()
    
    # 3. 将 service 实例交给测试函数
    yield redis_service
    
    # 4. 测试结束后，清空数据库并关闭连接
    print("\n--- Flushing test Redis DB... ---")
    await test_redis_client.flushdb()
    await redis_service.close()

@pytest.fixture(scope="function")
def arq_pool_mock(real_redis_service: RedisService) -> AsyncMock:
    """
    [最终修复版] Mocks arq_pool。它的 `enqueue_job` 方法使用 side_effect 
    来实际将作业加入测试 Redis，同时捕获真实的 JobResult 以便在测试中断言。
    """
    real_pool = ArqRedis(real_redis_service.client.connection_pool)
    
    mock = AsyncMock(spec=ArqRedis)
    
    # [核心修复] 创建一个包装函数作为 side_effect，用于捕获返回值
    async def side_effect_with_capture(*args, **kwargs) -> JobResult:
        # 调用真正的 enqueue_job 方法
        job_result = await real_pool.enqueue_job(*args, **kwargs)
        # 将真实的返回结果存储在 mock 对象的一个自定义属性上
        mock.captured_job_result = job_result
        # 必须返回结果，以便应用内部逻辑能正常工作
        return job_result

    mock.enqueue_job.side_effect = side_effect_with_capture
    
    # 也模拟 close 方法
    mock.close = AsyncMock(return_value=None)
    mock.connection_pool = real_pool.connection_pool
    
    return mock

@pytest.fixture
async def real_arq_pool() -> AsyncGenerator[ArqRedis, None]:
    """
    [最终版] 提供一个连接到测试 Redis 的真实 ArqRedis 实例。
    它创建自己的、专用的 Redis 客户端，并设置 `decode_responses=False`，
    以确保它能正确处理 ARQ 返回的二进制 msgpack 数据。
    """
    raw_redis_client_for_arq = aioredis.from_url(
        settings.REDIS_URL_TEST,
        decode_responses=False
    )
    pool = ArqRedis(raw_redis_client_for_arq.connection_pool)
    yield pool
    await pool.aclose()
    await raw_redis_client_for_arq.aclose()

@pytest.fixture
async def arq_worker_for_test(
    real_redis_service: RedisService,
    vector_manager_mock: AsyncMock,
) -> AsyncGenerator[Worker, None]:
    """
    [E2E] [最终版] 运行一个 ARQ Worker。
    此 fixture 自己管理其专用的 Redis 连接生命周期，
    并包含健壮的 teardown 逻辑以处理已知的 arq 关闭竞争条件。
    """
    from app.worker import TASK_FUNCTIONS
    
    # 1. 在 fixture 内部创建专用的 ARQ pool
    raw_redis_client_for_worker = aioredis.from_url(
        settings.REDIS_URL_TEST,
        decode_responses=False
    )
    arq_pool_for_worker = ArqRedis(raw_redis_client_for_worker.connection_pool)

    # 2. 定义 startup/shutdown
    async def test_startup(ctx):
        ctx['db_session_factory'] = TestSessionLocal
        ctx['redis_service'] = real_redis_service
        ctx['vector_manager'] = vector_manager_mock
        ctx['arq_pool'] = arq_pool_for_worker

    async def test_shutdown(ctx):
        if 'vector_manager' in ctx and hasattr(ctx['vector_manager'], 'shutdown'):
            await ctx['vector_manager'].shutdown()

    # 3. 初始化 Worker
    worker = Worker(
        functions=TASK_FUNCTIONS,
        cron_jobs=None,
        redis_pool=arq_pool_for_worker,
        on_startup=test_startup,
        on_shutdown=test_shutdown,
        health_check_interval=0,
    )
    
    worker_task = asyncio.create_task(worker.async_run())
    print("\n--- [E2E] In-process ARQ Worker started for test. ---")
    
    yield worker
    
    # 4. Teardown
    await worker.close() # 使用 .close() 以匹配你的 arq 版本
    
    try:
        await asyncio.wait_for(worker_task, timeout=5.0)
    except (AttributeError, asyncio.TimeoutError) as e:
        print(f"\n--- [E2E] INFO: Handled expected worker shutdown noise: {e} ---")
        pass

    # 5. 安全地关闭连接
    await arq_pool_for_worker.aclose()
    await raw_redis_client_for_worker.aclose()
    
    print("--- [E2E] In-process ARQ Worker stopped. ---")

async def wait_for_job_completion(arq_pool: ArqRedis, job_id: str, timeout: int = 20) -> Any:
    job = Job(job_id, arq_pool)
    start_time = time.time()
    print(f"\n--- [E2E] Waiting for ARQ job '{job_id}' to complete... ---")
    while time.time() - start_time < timeout:
        status = await job.status()
        if status == "complete":
            print(f"--- [E2E] Job '{job_id}' completed successfully. ---")
            return await job.result()
        elif status == "failed":
            result = await job.result(raise_exception=False)
            pytest.fail(f"ARQ job '{job_id}' failed with result: {result}")
        await asyncio.sleep(0.5)
    pytest.fail(f"ARQ job '{job_id}' timed out after {timeout} seconds.")

# ==============================================================================
# 3. 核心 AppContext 和 Client Fixtures
# ==============================================================================

@pytest.fixture(scope="function")
async def client(
    db_session: AsyncSession, 
    real_redis_service: RedisService,
    vector_manager_mock: VectorEngineManager,
    arq_pool_mock: AsyncMock,
    monkeypatch
) -> AsyncGenerator[AsyncClient, None]:
    """
    [最终重构版]
    这个 client fixture 使用最简洁、最健壮的依赖覆盖策略。
    它只覆盖最底层的依赖项，并让 FastAPI 的 DI 系统为我们构建所有上层依赖。
    """
    
    # --- 1. 定义最底层依赖的 Override 函数 ---
    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    if not db_session.in_transaction():
        await db_session.begin()
    runtime_connection = await db_session.connection()
    RuntimeSessionLocal = async_sessionmaker(
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
        bind=runtime_connection,
        class_=AsyncSession,
        join_transaction_mode="create_savepoint",
    )

    # 为当前测试的事件循环创建一个专用的 tenant data engine
    loop_local_tenant_engine = create_async_engine(settings.DATABASE_URL_TENANT_DATA, poolclass=NullPool)
    # 使用 monkeypatch 将全局引用的 engine 替换为我们新创建的这个
    monkeypatch.setattr('app.db.tenant_db_session.tenant_data_engine', loop_local_tenant_engine)

    # --- 2. 将 Override 应用到 FastAPI app ---
    app.dependency_overrides[get_db] = override_get_db
    
    # --- 3. 模拟应用启动时设置的 app.state ---
    app.state.permission_hierarchy = await preload_permission_hierarchy(db_session)
    app.state.redis_service = real_redis_service
    app.state.vector_manager = vector_manager_mock
    app.state.arq_pool = arq_pool_mock
    app.state.db_session_factory = RuntimeSessionLocal

    # --- 4. 运行测试 ---
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    
    # --- 5. 测试结束后清理 ---
    app.dependency_overrides.clear()
    
    # 清理模拟的服务
    if hasattr(app.state, 'vector_manager') and app.state.vector_manager:
        await app.state.vector_manager.shutdown()
    if hasattr(app.state, 'arq_pool') and app.state.arq_pool:
        await app.state.arq_pool.close()

    # 清理我们为测试创建的 tenant engine
    await loop_local_tenant_engine.dispose()
    
    # 删除 app.state 上的属性以保持隔离性
    if hasattr(app.state, 'redis_service'): del app.state.redis_service
    if hasattr(app.state, 'vector_manager'): del app.state.vector_manager
    if hasattr(app.state, 'arq_pool'): del app.state.arq_pool
    if hasattr(app.state, 'db_session_factory'): del app.state.db_session_factory

@pytest.fixture(scope="function")
async def prod_like_client(
    db_session: AsyncSession,
    real_redis_service: RedisService,
    vector_manager_mock: VectorEngineManager,
    arq_pool_mock: AsyncMock,
    monkeypatch
) -> AsyncGenerator[AsyncClient, None]:
    """
    生产一致性基线：每个请求使用独立 AsyncSession + request 事务。
    该 fixture 主要用于暴露跨请求场景下的隐式懒加载风险。
    """
    if not db_session.in_transaction():
        await db_session.begin()
    shared_connection = await db_session.connection()

    ProdLikeSessionLocal = async_sessionmaker(
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
        bind=shared_connection,
        class_=AsyncSession,
        join_transaction_mode="create_savepoint",
    )

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with ProdLikeSessionLocal() as session:
            async with session.begin():
                yield session

    loop_local_tenant_engine = create_async_engine(settings.DATABASE_URL_TENANT_DATA, poolclass=NullPool)
    monkeypatch.setattr('app.db.tenant_db_session.tenant_data_engine', loop_local_tenant_engine)

    app.dependency_overrides[get_db] = override_get_db

    async with ProdLikeSessionLocal() as bootstrap_session:
        app.state.permission_hierarchy = await preload_permission_hierarchy(bootstrap_session)
    app.state.redis_service = real_redis_service
    app.state.vector_manager = vector_manager_mock
    app.state.arq_pool = arq_pool_mock
    app.state.db_session_factory = ProdLikeSessionLocal

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()

    if hasattr(app.state, 'vector_manager') and app.state.vector_manager:
        await app.state.vector_manager.shutdown()
    if hasattr(app.state, 'arq_pool') and app.state.arq_pool:
        await app.state.arq_pool.close()

    await loop_local_tenant_engine.dispose()

    if hasattr(app.state, 'redis_service'): del app.state.redis_service
    if hasattr(app.state, 'vector_manager'): del app.state.vector_manager
    if hasattr(app.state, 'arq_pool'): del app.state.arq_pool
    if hasattr(app.state, 'db_session_factory'): del app.state.db_session_factory

# 提供一个通用的、可用于物理验证的 tenant data connection
@pytest.fixture(scope="function")
async def tenant_data_db_conn() -> AsyncGenerator[AsyncConnection, None]:
    """
    Provides a direct connection to the tenant data plane DB for physical verification.
    It correctly uses the monkeypatched engine because it imports it *after* the patch is applied.
    """
    # 延迟导入以确保获取到的是被 monkeypatch 后的 engine
    from app.db.tenant_db_session import tenant_data_engine
    async with tenant_data_engine.connect() as conn:
        yield conn

# ==============================================================================
# 4. 可复用的高级工厂 Fixtures (User, Auth)
# ==============================================================================

@dataclass
class UserContext:
    """一个封装了ORM对象和测试所需上下文的数据容器。"""
    user: User
    password: str
    personal_workspace: Workspace

@pytest.fixture(scope="function")
def user_data_factory():
    """一个工厂fixture，用于生成唯一的用户数据字典。"""
    def _user_data_factory(email: str = None, password: str = None, **kwargs):
        return {
            "email": email or f"test_{uuid.uuid4()}@example.com",
            "password": password or f"a-secure-password-{uuid.uuid4()}",
            "nick_name": "Test User",
            **kwargs
        }
    return _user_data_factory

@pytest.fixture(scope="function")
async def registered_user_factory(client: AsyncClient, db_session: AsyncSession, user_data_factory):
    """
    [最终重构版] 一个工厂fixture，用于通过API注册一个用户，并根据需要正确地模拟套餐升级。
    它现在直接操作正确的 `Membership` 模型，以确保测试数据的准确性。
    """
    async def _factory(
        email: str = None, 
        password: str = None, 
        plan_tier: PlanTier = PlanTier.FREE,
        initial_balance: Decimal = Decimal('0.0'),
        **kwargs
    ) -> UserContext:
        # 1. 通过API注册用户，该用户将自动获得 FREE 套餐
        user_data = user_data_factory(email=email, password=password, **kwargs)
        response = await client.post("/api/v1/identity/register", json=user_data)
        assert response.status_code == status.HTTP_201_CREATED, f"User registration failed: {response.text}"
        
        # 2. 从数据库获取新创建的用户及其关联对象
        user_dao = UserDao(db_session)
        final_user = await user_dao.get_one(
            where={"email": user_data["email"]},
            # 预加载 membership 以便后续操作
            withs=["billing_account", "membership"] 
        )
        assert final_user is not None
        assert final_user.billing_account is not None
        assert final_user.membership is not None, "User should have a FREE membership after registration."

        # 3. 如果提供了初始余额，则为其充值
        if initial_balance > 0:
            final_user.billing_account.balance = initial_balance
            await db_session.flush()
            # 刷新以确保 user 对象上的 billing_account 关系也更新了
            await db_session.refresh(final_user, ['billing_account'])

        # 3-1. [核心修复] 如果需要非免费套餐，则正确地模拟套餐升级
        if plan_tier != PlanTier.FREE:
            # 找到目标套餐对应的 Product
            product_dao = ProductDao(db_session)
            target_product = await product_dao.get_one(where={"plan_tier": plan_tier, "type": ProductType.MEMBERSHIP})
            assert target_product, f"Product for plan tier '{plan_tier.value}' not found in test DB. Seeding issue?"

            # 直接更新 Membership 记录，这是用户套餐的唯一权威来源
            final_user.membership.product_id = target_product.id
            final_user.membership.plan_tier = target_product.plan_tier
            final_user.membership.role_id = target_product.granted_role_id
            final_user.membership.current_period_end = datetime.utcnow() + timedelta(days=30)
            await db_session.flush()

            # 刷新 user 对象以确保其关系是最新的
            await db_session.refresh(final_user, attribute_names=['membership'])

        # 4. 获取用户的个人工作空间
        workspace_dao = WorkspaceDao(db_session)
        personal_workspace = await workspace_dao.get_one(
            where={"owner_user_id": final_user.id}
        )
        assert personal_workspace is not None

        # 5. 返回封装后的上下文对象
        return UserContext(user=final_user, password=user_data["password"], personal_workspace=personal_workspace)
        
    return _factory

@pytest.fixture(scope="function")
async def auth_headers_factory(client: AsyncClient):
    """一个工厂fixture，用于为一个给定的 UserContext 获取认证头。"""
    async def _factory(test_context: UserContext) -> dict:
        login_data = {
            "grant_type": "password",
            "identifier": test_context.user.email,
            "password": test_context.password,
        }
        response = await client.post("/api/v1/identity/token", json=login_data)
        assert response.status_code == 200, f"Failed to log in user {test_context.user.email}: {response.text}"
        token = response.json()["data"]["access_token"]
        return {"Authorization": f"Bearer {token}"}
    return _factory

@pytest.fixture(scope="function")
async def get_billable_cost(db_session: AsyncSession) -> Decimal:
    async def _factory(
        feature_name: str,
        usage: str
    ) -> Decimal:
        """
        [动态价格 Fixture] 在测试运行时动态获取计费工具的单次执行成本。
        这使得测试对价格变化具有鲁棒性。
        """
        # 1. 获取计费特性
        feature_dao = FeatureDao(db_session)
        feature = await feature_dao.get_one(
            where={"name": feature_name},
            # 预加载价格信息以提高效率
            withs=[{"name": "product", "withs": ["prices"]}]
        )
        assert feature, f"Default billable feature '{feature_name}' not found."
        
        # 2. 使用 PricingProvider 解析价格
        # 注意：这里我们不能用 mock 的 redis，但 PricingProvider 设计得很好，可以没有 redis 也能工作
        pricing_provider = PricingProvider(db_session, redis=None) 
        price_info = pricing_provider._parse_price_from_feature(feature, Currency.CNY) # 假设测试站点货币为 CNY
        
        assert price_info and price_info.amount, "Price for feature not configured correctly."
        
        # 3. 返回单次执行的成本
        return (Decimal(usage) / Decimal(price_info.unit_count)) * price_info.amount
        
    return _factory
    
# ==============================================================================
# 5. [重构后] 通用实体 Fixtures
# ==============================================================================

# --- User Fixtures ---

@pytest.fixture
async def registered_user_with_pro(registered_user_factory: Callable) -> UserContext:
    """提供一个标准的个人用户上下文（PRO套餐）。"""
    return await registered_user_factory(plan_tier=PlanTier.PRO, initial_balance=Decimal('10'))

@pytest.fixture
async def registered_user_with_free(registered_user_factory: Callable) -> UserContext:
    """提供另一个独立的用户上下文（默认FREE套餐）。"""
    return await registered_user_factory()

@pytest.fixture
async def registered_user_with_team(registered_user_factory: Callable) -> UserContext:
    """提供一个有权限创建团队的用户上下文（TEAM套餐）。"""
    return await registered_user_factory(plan_tier=PlanTier.TEAM)

# 方便地提取 ORM 对象，以向后兼容那些不需要密码的测试
@pytest.fixture
def user(registered_user_with_pro: UserContext) -> User:
    return registered_user_with_pro.user

@pytest.fixture
def another_user(registered_user_with_free: UserContext) -> User:
    return registered_user_with_free.user

@pytest.fixture
def user_with_team_plan(registered_user_with_team: UserContext) -> User:
    return registered_user_with_team.user

# --- Workspace & Team Fixtures ---

@pytest.fixture
async def created_team(client: AsyncClient, auth_headers_factory: Callable, registered_user_with_team: UserContext, db_session: AsyncSession) -> Team:
    """通过API创建一个团队。"""
    headers = await auth_headers_factory(registered_user_with_team)
    payload = {"name": f"Test Team {uuid.uuid4().hex[:6]}"}
    response = await client.post("/api/v1/teams", json=payload, headers=headers)
    assert response.status_code == status.HTTP_201_CREATED
    team_uuid = response.json()["data"]["uuid"]
    return await TeamDao(db_session).get_one(where={"uuid": team_uuid})


@pytest.fixture
async def team_workspace(client: AsyncClient, auth_headers_factory: Callable, registered_user_with_team: UserContext, created_team: Team, db_session: AsyncSession) -> Workspace:
    """通过API为团队创建一个工作空间。"""
    headers = await auth_headers_factory(registered_user_with_team)
    payload = {"name": "Shared Team Workspace", "owner_team_uuid": created_team.uuid}
    response = await client.post("/api/v1/workspaces", json=payload, headers=headers)
    assert response.status_code == status.HTTP_201_CREATED
    ws_uuid = response.json()["data"]["uuid"]
    return await WorkspaceDao(db_session).get_one(where={"uuid": ws_uuid})

# --- Project Fixture ---

@pytest.fixture
async def created_project_in_personal_ws(client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, db_session: AsyncSession) -> Project:
    """在用户的个人工作空间中创建一个项目。"""
    headers = await auth_headers_factory(registered_user_with_pro)
    personal_workspace = registered_user_with_pro.personal_workspace
    payload = {"name": "My Personal Test Project", "main_application_type": "uiapp"}
    
    response = await client.post(f"/api/v1/workspaces/{personal_workspace.uuid}/projects", json=payload, headers=headers)
    assert response.status_code == status.HTTP_201_CREATED, "Fixture setup failed: Project creation failed"
    
    project_uuid = response.json()["data"]["uuid"]
    project = await ProjectDao(db_session).get_one(where={"uuid": project_uuid})
    assert project is not None
    return project

@pytest.fixture
async def created_project_in_team_ws(
    client: AsyncClient, 
    auth_headers_factory: Callable, 
    registered_user_with_team: UserContext, 
    team_workspace: Workspace, 
    db_session: AsyncSession
) -> Project:
    """在团队工作空间中创建一个项目，用于测试团队协作场景。"""
    headers = await auth_headers_factory(registered_user_with_team)
    payload = {"name": "Shared Team Project", "main_application_type": "agent"}
    
    response = await client.post(f"/api/v1/workspaces/{team_workspace.uuid}/projects", json=payload, headers=headers)
    assert response.status_code == status.HTTP_201_CREATED, "Fixture setup failed: Team project creation failed"
    
    project_uuid = response.json()["data"]["uuid"]
    project = await ProjectDao(db_session).get_one(where={"uuid": project_uuid})
    assert project is not None
    return project

@pytest.fixture
def created_resource_factory(
    client: AsyncClient,
    auth_headers_factory: Callable,
    registered_user_with_pro: UserContext,
    db_session: AsyncSession
) -> Callable:
    """
    [工厂 Fixture] 一个可复用的工厂，用于通过API创建任何类型的资源。
    可被其他测试文件（如 test_tool_execution.py）导入和使用。
    """
    async def _factory(resource_type: str) -> Resource:
        headers = await auth_headers_factory(registered_user_with_pro)
        payload = {
            "name": f"My First {resource_type.capitalize()}",
            "resource_type": resource_type,
            "description": f"A test resource of type {resource_type}."
        }
        
        personal_workspace = registered_user_with_pro.personal_workspace
        response = await client.post(f"/api/v1/workspaces/{personal_workspace.uuid}/resources", json=payload, headers=headers)
        assert response.status_code == status.HTTP_201_CREATED, f"Failed to create resource of type {resource_type}: {response.text}"
        
        resource_uuid = response.json()["data"]["uuid"]
        resource = await ResourceDao(db_session).get_resource_details_by_uuid(resource_uuid) # 使用能加载多态的DAO方法
        assert resource is not None
        return resource
        
    return _factory

# ==============================================================================
# 6. 服务层测试辅助 Fixtures
# ==============================================================================

@pytest.fixture
async def workspace_instance_factory(
    client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext,
    db_session: AsyncSession, created_resource: Resource
) -> Callable:
    """
    [特化 Fixture] 在通用 `workspace_instance` 的基础上，为其配置领域的属性。
    这是所有资源执行测试的前置条件。
    """
    async def _factory(instance_config: Dict[str, Any]) -> ResourceInstance:
        headers = await auth_headers_factory(registered_user_with_pro)
        workspace_uuid = created_resource.workspace_instance.uuid
        response = await client.put(f"/api/v1/instances/{workspace_uuid}", json=instance_config, headers=headers)
        assert response.status_code == status.HTTP_200_OK
        return await ResourceInstanceDao(db_session).get_by_uuid(workspace_uuid)
    return _factory
    
@pytest.fixture
async def publish_instance_factory(
    client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, db_session: AsyncSession
) -> Callable:
    """
    [通用方法] 返回一个工厂函数，用于发布任何给定的工作区实例。
    """
    async def _factory(workspace_instance_uuid: str, version_tag: str) -> ResourceInstance:
        headers = await auth_headers_factory(registered_user_with_pro)
        payload = {"version_tag": version_tag}
        response = await client.post(f"/api/v1/instances/{workspace_instance_uuid}/publish", json=payload, headers=headers)
        assert response.status_code == status.HTTP_201_CREATED, f"Failed to publish version {version_tag}"
        
        published_uuid = response.json()["data"]["uuid"]
        return await ResourceInstanceDao(db_session).get_by_uuid(published_uuid)
    return _factory

@pytest.fixture
async def archive_instance_factory(
    client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, db_session: AsyncSession
) -> Callable:
    """
    [通用方法] 返回一个工厂函数，用于发布任何给定的工作区实例。
    """
    async def _factory(instance_uuid: str) -> ResourceInstance:
        headers = await auth_headers_factory(registered_user_with_pro)
        payload = {}
        response = await client.post(f"/api/v1/instances/{instance_uuid}/archive", json=payload, headers=headers)
        assert response.status_code == status.HTTP_200_OK, f"Failed to archive version uuid {instance_uuid}"
        
        archived_uuid = response.json()["data"]["uuid"]
        return await ResourceInstanceDao(db_session).get_by_uuid(archived_uuid)
    return _factory

@pytest.fixture(scope="function")
async def credential_payload_factory(db_session: AsyncSession) -> Callable:
    """一个工厂，用于生成模块凭证的 API payload。"""
    async def _factory(provider_name: str = "openai", label: str = "My OpenAI Key", value: str = "sk-test-1234567890"):
        provider_dao = ServiceModuleProviderDao(db_session)
        provider = await provider_dao.get_by_name(provider_name)
        return {"provider_id": provider.id, "label": label, "value": value}
    return _factory

@pytest.fixture
def app_context_factory(
    db_session: AsyncSession,
    real_redis_service: RedisService,
    vector_manager_mock: VectorEngineManager,
    arq_pool_mock: AsyncMock
) -> Callable:
    """
    一个工厂 Fixture，用于为服务层单元测试构建一个完整的 AppContext。
    """
    async def _factory(actor: Optional[User]=None) -> AppContext:
        auth_context = None
        if not db_session.in_transaction():
            await db_session.begin()
        runtime_connection = await db_session.connection()
        RuntimeSessionLocal = async_sessionmaker(
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
            bind=runtime_connection,
            class_=AsyncSession,
            join_transaction_mode="create_savepoint",
        )

        if actor:
            permission_hierarchy = await preload_permission_hierarchy(db_session)

            auth_context = await get_base_auth_context(
                user_uuid=actor.uuid,
                db=db_session,
                redis_service=real_redis_service,
                permission_hierarchy=permission_hierarchy
            )

        return AppContext(
            db=db_session,
            db_session_factory=RuntimeSessionLocal,
            auth=auth_context,
            redis_service=real_redis_service,
            vector_manager=vector_manager_mock,
            arq_pool=arq_pool_mock
        )
    return _factory
