from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# 1. [核心变更] 从中央配置模块导入 'settings' 实例
#    不再从任何地方导入硬编码的 'mysql' 字典
from app.core.config import settings

# 2. [增强] 为数据库引擎添加生产环境推荐的配置
#    这能显著提升数据库连接的稳定性和性能
engine = create_async_engine(
    # 直接使用在 config.py 中拼接好的 DATABASE_URL
    settings.DATABASE_URL,
    
    # 推荐的连接池配置：
    pool_pre_ping=True,      # 在每次从连接池获取连接时，测试其连通性，防止拿到失效连接
    pool_recycle=3600,       # 每隔1小时（3600秒）回收连接，防止因长时间空闲被MySQL服务器断开
    # pool_size=10,          # [可选] 连接池中的常备连接数，可根据负载调整
    # max_overflow=20,       # [可选] 连接池在高并发时可额外创建的最大连接数
)

# 3. [不变] SessionLocal 和 get_db 的定义保持不变，它们已经是最佳实践
SessionLocal = sessionmaker(
    autocommit=False, 
    autoflush=False, 
    expire_on_commit=False, 
    bind=engine, 
    class_=AsyncSession
)

# 依赖项：为每个API请求提供一个独立的数据库会话
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that provides a transactional scope around a request.
    This is the modern, idiomatic SQLAlchemy 2.0 way.
    """
    async with SessionLocal() as session:
        # The 'async with session.begin():' block handles transactions automatically.
        # It commits if the block completes without error.
        # It rolls back if any exception occurs within the block.
        async with session.begin():
            yield session