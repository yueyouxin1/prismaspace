# src/app/models/resource/knowledge/knowledge_chunk.py
import enum
from sqlalchemy import (
    Column, Integer, String, Text, ForeignKey, JSON, Enum as PgEnum
)
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.utils.id_generator import generate_uuid

class ChunkProcessingStatus(enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    
class KnowledgeChunk(Base):
    """代表文档被切分、向量化后的一个文本块，是RAG检索的基本单元。"""
    __tablename__ = 'ai_knowledge_chunks'

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True, nullable=False)
    
    document_id = Column(Integer, ForeignKey('ai_knowledge_documents.id', ondelete='CASCADE'), nullable=False, index=True)

    # --- 核心内容 ---
    content = Column(Text, nullable=False)
    token_count = Column(Integer, nullable=False)
    
    # --- 向量数据库链接与元数据 ---
    vector_id = Column(String(255), nullable=True, index=True, comment="The unique ID of this chunk in the physical vector store")
    context = Column(JSON, nullable=True, comment="Structured context for enrichment (e.g., page number, section)")
    payload = Column(JSON, nullable=True, comment="Vector payload")

    status = Column(PgEnum(ChunkProcessingStatus), nullable=False, default=ChunkProcessingStatus.PENDING, index=True)
    error_message = Column(Text, nullable=True)

    # --- 关系 ---
    document = relationship("KnowledgeDocument", back_populates="chunks")