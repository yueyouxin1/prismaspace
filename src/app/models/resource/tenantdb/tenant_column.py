# src/app/models/resource/tenantdb/tenant_column.py

import enum
from sqlalchemy import (
    Column, Text, Integer, String, Boolean, JSON, Enum as PgEnum, ForeignKey,
    UniqueConstraint
)
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.utils.id_generator import generate_uuid

class TenantDataType(enum.Enum):
    TEXT = "text"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    TIMESTAMP = "timestamp"
    JSON = "json"

class TenantColumn(Base):
    __tablename__ = 'ai_tenant_columns'

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True, nullable=False)
    table_id = Column(Integer, ForeignKey('ai_tenant_tables.id', ondelete='CASCADE'), nullable=False, index=True)
    
    # [增强] 统一标识符长度为63
    name = Column(String(63), nullable=False, comment="列名，必须符合PostgreSQL标识符规范")
    label = Column(String(255), nullable=True, comment="在UI中展示的友好名称")
    description = Column(Text, nullable=True, comment="该列的详细描述或帮助文本")
    
    data_type = Column(PgEnum(TenantDataType), nullable=False)
    
    # --- Constraints ---
    # [增强] 显式化主键定义
    is_primary_key = Column(Boolean, nullable=False, default=False)
    is_nullable = Column(Boolean, nullable=False, default=True)
    is_unique = Column(Boolean, nullable=False, default=False)
    is_indexed = Column(Boolean, nullable=False, default=False, comment="是否为该列创建索引以加速查询")
    # [增强] 使用JSON确保类型安全
    default_value = Column(JSON, nullable=True, comment="类型安全的默认值 (e.g., 123, 'abc', true)")
    is_vector_enabled = Column(Boolean, nullable=False, default=False, comment="是否为该列启用向量嵌入和检索")

    table = relationship("TenantTable", back_populates="columns")

    __table_args__ = (
        UniqueConstraint('table_id', 'name', name='uq_tenant_table_column_name'),
    )