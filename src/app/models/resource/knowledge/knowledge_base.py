# src/app/models/resource/knowledge/knowledge_base.py
from sqlalchemy import Column, Integer, String, JSON, ForeignKey
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.models.resource.base import ResourceInstance

class KnowledgeBase(ResourceInstance):
    """
    KnowledgeBase 资源实例，继承通用版本模型。
    它代表一个隔离的、可查询的知识库。
    """
    __tablename__ = 'ai_knowledge_bases'

    version_id = Column(Integer, ForeignKey('ai_resource_instances.id', ondelete='CASCADE'), primary_key=True)
    
    # --- 物理层链接与配置 ---
    collection_name = Column(String(255), nullable=False, index=True, comment="The unique collection name in the physical vector store")
    # 指向 VectorEngineManager 中配置的物理引擎别名
    engine_alias = Column(String(50), nullable=False, default='default', index=True, comment="The alias of the physical vector engine to use (e.g., 'default', 'high_perf')")

    # --- 核心配置 ---
    embedding_module_version_id = Column(Integer, ForeignKey('service_module_versions.id'), nullable=False)
    config = Column(JSON, nullable=True, comment="The declarative configuration for the data processing pipeline.")

    # --- 关系 ---
    embedding_module_version = relationship("ServiceModuleVersion", lazy="joined")
    documents = relationship(
        "KnowledgeDocument",
        secondary="knowledge_base_version_documents",
        back_populates="versions"
    )

    __mapper_args__ = {
        'polymorphic_identity': 'knowledge',
    }

class KnowledgeBaseVersionDocuments(Base):
    __tablename__ = 'knowledge_base_version_documents'
    version_id = Column(Integer, ForeignKey('ai_knowledge_bases.version_id', ondelete='CASCADE'), primary_key=True)
    document_id = Column(Integer, ForeignKey('ai_knowledge_documents.id', ondelete='CASCADE'), primary_key=True)