# src/worker/main.py

from arq import create_pool
from arq.connections import RedisSettings
from app.db.session import SessionLocal, engine
from app.services.redis_service import RedisService
from app.engine.vector.main import VectorEngineManager, VectorEngineConfig
from app.core.config import settings

TASK_FUNCTIONS = []
CRON_JOBS = []

def get_redis_settings():
    """统一的 Redis 配置获取函数"""
    return RedisSettings(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        password=settings.REDIS_PASSWORD,
        database=settings.REDIS_DB
    )

async def startup(ctx):
    """Worker 进程启动时，创建依赖工厂。"""
    ctx['db_session_factory'] = SessionLocal
    ctx['redis_service'] = RedisService()
    engine_configs = [VectorEngineConfig(**config_dict) for config_dict in settings.VECTOR_ENGINE_CONFIGS]
    vector_manager = VectorEngineManager(configs=engine_configs)
    await vector_manager.startup()
    ctx['vector_manager'] = vector_manager
    redis_settings = get_redis_settings()
    ctx['arq_pool'] = await create_pool(redis_settings)
    print("ARQ Worker started up, database session factory is ready.")

async def shutdown(ctx):
    """Worker 进程关闭时，清理资源。"""
    redis_service = ctx['redis_service']
    vector_manager = ctx['vector_manager']
    await redis_service.close()
    await vector_manager.shutdown()
    await ctx['arq_pool'].aclose()
    await engine.dispose()
    print("ARQ Worker shut down, database engine disposed.")

class WorkerSettings:
    """ARQ Worker 的主配置。"""
    functions = TASK_FUNCTIONS
    cron_jobs = CRON_JOBS
    on_startup = startup
    on_shutdown = shutdown
    # 从 settings.py 中读取 Redis 配置
    redis_settings = get_redis_settings()