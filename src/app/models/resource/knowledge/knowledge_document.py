# src/app/models/resource/knowledge/knowledge_document.py
import enum
from sqlalchemy import (
    Column, Integer, String, Text, Enum as PgEnum, ForeignKey,
    DateTime, func, JSON
)
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.utils.id_generator import generate_uuid

class DocumentProcessingStatus(enum.Enum):
    PENDING = "pending"          # 等待处理
    UPLOADING = "uploading"      # 文件正在上传到存储
    PROCESSING = "processing"    # 正在解析、切块、嵌入
    COMPLETED = "completed"      # 处理完成，可供查询
    FAILED = "failed"            # 处理失败

class KnowledgeDocument(Base):
    """代表用户上传到某个KnowledgeBase实例的原始文档。"""
    __tablename__ = 'ai_knowledge_documents'

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True, nullable=False)

    # --- 文件元数据 ---
    file_name = Column(String(255), nullable=False)
    file_type = Column(String(50), nullable=True, comment="MIME type of the file")
    file_size = Column(Integer, nullable=True, comment="File size in bytes")
    source_uri = Column(String(1024), nullable=True, comment="URI to the original file in storage (e.g., S3)")
    
    # --- 处理状态与统计 ---
    status = Column(PgEnum(DocumentProcessingStatus), nullable=False, default=DocumentProcessingStatus.PENDING, index=True)
    error_message = Column(Text, nullable=True)
    chunk_count = Column(Integer, default=0)
    token_count = Column(Integer, default=0)

    # --- 时间戳 ---
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    processed_at = Column(DateTime, nullable=True)

    # --- 关系 ---
    chunks = relationship("KnowledgeChunk", back_populates="document", cascade="all, delete-orphan")
    # 反向关系，一个文档可以被多个版本引用
    versions = relationship(
        "KnowledgeBase",
        secondary="knowledge_base_version_documents",
        back_populates="documents"
    )