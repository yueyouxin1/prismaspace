# src/app/models/resource/tenantdb/tenantdb.py

from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from app.models.resource.base import ResourceInstance

class TenantDB(ResourceInstance):
    """
    TenantDB 资源实例，继承通用的版本管理模型。
    它代表一个隔离的、多表的关系型数据库环境。
    """
    __tablename__ = 'ai_tenant_dbs'

    version_id = Column(Integer, ForeignKey('ai_resource_instances.id', ondelete='CASCADE'), primary_key=True)
    
    # 多个版本(Instance)可以指向同一个物理 schema_name
    schema_name = Column(String(63), nullable=False, index=True, comment="在租户数据平面DB中对应的Schema名称")

    # --- Relationships ---
    tables = relationship(
        "TenantTable", 
        back_populates="tenant_db", 
        cascade="all, delete-orphan"
    )

    __mapper_args__ = {
        'polymorphic_identity': 'tenantdb',
    }