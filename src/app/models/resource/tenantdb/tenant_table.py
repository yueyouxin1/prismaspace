# src/app/models/resource/tenantdb/tenant_table.py

from sqlalchemy import Column, Integer, String, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.utils.id_generator import generate_uuid

class TenantTable(Base):
    __tablename__ = 'ai_tenant_tables'

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True, nullable=False)
    tenantdb_id = Column(Integer, ForeignKey('ai_tenant_dbs.version_id', ondelete='CASCADE'), nullable=False, index=True)
    
    # [增强] 统一标识符长度为63
    name = Column(String(63), nullable=False, comment="表名，必须符合PostgreSQL标识符规范")
    label = Column(String(255), nullable=True, comment="在UI中展示的友好名称")
    description = Column(Text, nullable=True)

    tenant_db = relationship("TenantDB", back_populates="tables")
    columns = relationship(
        "TenantColumn", 
        back_populates="table", 
        cascade="all, delete-orphan",
        order_by="TenantColumn.id"
    )

    __table_args__ = (
        UniqueConstraint('tenantdb_id', 'name', name='uq_tenantdb_table_name'),
    )