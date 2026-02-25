# src/app/db/tenant_db_session.py

from sqlalchemy.ext.asyncio import create_async_engine
from app.core.config import settings

# [关键] 为租户数据平面创建一个独立的、高性能的数据库引擎
tenant_data_engine = create_async_engine(
    settings.DATABASE_URL_TENANT_DATA,
    pool_pre_ping=True,
    pool_recycle=3600,
)