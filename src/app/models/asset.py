# src/app/models/asset.py

import enum
from sqlalchemy import (
    Column, Integer, String, Text, JSON, Boolean, Enum, ForeignKey,
    DateTime, func, Index, BigInteger, CheckConstraint
)
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.utils.id_generator import generate_uuid

class AssetType(str, enum.Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    OTHER = "other"

class AssetStatus(str, enum.Enum):
    PENDING = "pending"       # 等待上传
    ACTIVE = "active"         # 已上传/可用
    ARCHIVED = "archived"     # 已归档 (逻辑删除)

class IntelligenceStatus(str, enum.Enum):
    PENDING = "pending"       # 等待处理
    PROCESSING = "processing" # 处理中
    COMPLETED = "completed"   # 处理完成
    FAILED = "failed"         # 处理失败

class SoftDeleteMixin:
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    deleted_at = Column(DateTime, nullable=True)

class AssetFolder(Base, SoftDeleteMixin):
    """
    素材文件夹表 - 用于层级化管理用户资产。
    """
    __tablename__ = 'assets_folders'

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True, nullable=False)
    
    # 归属与组织
    workspace_id = Column(Integer, ForeignKey('ai_workspaces.id', ondelete='CASCADE'), nullable=False, index=True)
    creator_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    
    # 层级关系
    parent_id = Column(Integer, ForeignKey('assets_folders.id', ondelete='CASCADE'), nullable=True, index=True)
    
    name = Column(String(255), nullable=False)
    
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    # Relationships
    parent = relationship("AssetFolder", remote_side=[id], back_populates="children")
    children = relationship("AssetFolder", back_populates="parent", cascade="all, delete-orphan")
    assets = relationship("Asset", back_populates="folder", cascade="all, delete-orphan")
    workspace = relationship("Workspace")
    
class AssetIntelligence(Base):
    """
    [共享层] 智能元数据表。
    基于内容 Hash (ETag/MD5) 去重，全平台共享 AI 分析结果。
    """
    __tablename__ = 'assets_intelligence'

    # 使用 Hash 作为主键
    content_hash = Column(String(128), primary_key=True, comment="文件内容权威Hash (通常为OSS ETag)")
    
    # AI 分析状态
    status = Column(Enum(IntelligenceStatus), default=IntelligenceStatus.PENDING, nullable=False)
    
    # AI 产生的元数据 (OCR, Caption, Transcript, Summary)
    meta = Column(JSON, nullable=True, comment="AI分析结果")
    
    # 向量化状态 (sys_assets_index)
    is_vector_indexed = Column(Boolean, default=False, index=True)
    
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class Asset(Base, SoftDeleteMixin):
    """
    [应用层] 逻辑资产表。
    明确归属于 Workspace，记录物理路径，并关联智能数据。
    """
    __tablename__ = 'assets'

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True, nullable=False)
    
    # 归属与组织
    workspace_id = Column(Integer, ForeignKey('ai_workspaces.id', ondelete='CASCADE'), nullable=False, index=True)
    folder_id = Column(Integer, ForeignKey('assets_folders.id', ondelete='SET NULL'), nullable=True, index=True)
    creator_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    
    # 物理信息 (Physical Isolation)
    storage_provider = Column(String(50), nullable=False)
    real_name = Column(String(1024), nullable=False, comment="存储桶中的完整物理Key")
    url = Column(String(2048), nullable=False, comment="访问URL")
    
    # 元数据
    name = Column(String(255), nullable=False)
    size = Column(BigInteger, nullable=False)
    mime_type = Column(String(100), nullable=False)
    type = Column(Enum(AssetType), nullable=False, default=AssetType.OTHER, index=True)
    
    # [核心关联] 通过 Hash 指向智能数据 (Nullable, 因为可能OSS还未回调Hash，或者文件无需AI)
    content_hash = Column(String(128), ForeignKey('assets_intelligence.content_hash'), nullable=True, index=True)

    # 状态
    status = Column(Enum(AssetStatus), default=AssetStatus.PENDING, index=True)
    
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    # Relationships
    workspace = relationship("Workspace")
    folder = relationship("AssetFolder", back_populates="assets")
    intelligence = relationship("AssetIntelligence") # 单向关联即可