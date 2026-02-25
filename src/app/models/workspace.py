import enum
from sqlalchemy import (
    Column, Integer, String, Text, JSON, Boolean, Enum, ForeignKey,
    DateTime, func, Index, DECIMAL, UniqueConstraint, CheckConstraint
)
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.utils.id_generator import generate_uuid

class WorkspaceStatus(enum.Enum): ACTIVE = "active"; SUSPENDED = "suspended"; ARCHIVED = "archived"

class ProjectVisibility(enum.Enum): PRIVATE = "private"; WORKSPACE = "workspace"; PUBLIC = "public"

class ProjectStatus(enum.Enum): DRAFT = "draft"; ACTIVE = "active"; ARCHIVED = "archived"; TEMPLATE = "template"

class Workspace(Base):
    """
    工作空间表 - 纯粹的创作容器，用于组织项目和资源。
    """
    __tablename__ = 'ai_workspaces'
    
    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), nullable=False, unique=True, index=True, default=generate_uuid)
    name = Column(String(255), nullable=False)
    # 增强/QoL
    avatar = Column(String(512), nullable=True, comment="工作空间头像URL")
    # [关键变更] Workspace 的归属
    # 它可以属于一个用户（个人空间），也可以属于一个团队（团队空间）
    # 这两个外键是互斥的，应该在应用层保证只有一个被设置
    owner_user_id = Column(Integer, ForeignKey('users.id'), nullable=True, index=True)
    owner_team_id = Column(Integer, ForeignKey('teams.id'), nullable=True, index=True)
    
    # 必要
    status = Column(Enum(WorkspaceStatus), nullable=False, default=WorkspaceStatus.ACTIVE, comment="工作空间状态 (活跃, 暂停, 归档)")
    
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    # 素材库配置
    # 结构示例: { "enable_ai_processing": True, "limit_types": ["image", "pdf"] }
    asset_config = Column(JSON, nullable=True, default={"enable_ai_processing": True}, comment="素材库策略配置")
    
    user_owner = relationship("User", lazy="joined")
    team = relationship("Team", back_populates="workspaces", lazy="joined")
    projects = relationship("Project", back_populates="workspace", cascade="all, delete-orphan")
    resources = relationship("Resource", back_populates="workspace", cascade="all, delete-orphan")
    roles = relationship("Role", back_populates="workspace", cascade="all, delete-orphan")
    __table_args__ = (
        CheckConstraint(
            '(owner_user_id IS NOT NULL AND owner_team_id IS NULL) OR '
            '(owner_user_id IS NULL AND owner_team_id IS NOT NULL)',
            name='ck_workspace_owner_exclusive'
        ),
    )

    @property
    def billing_owner(self):
        """获取工作空间的账单所有者（用户或团队）"""
        if not self.user_owner and not self.team:
            raise RuntimeError("Billing owner relationships not loaded. Use eager loading.")
        
        return self.user_owner or self.team

class Project(Base):
    """项目表 - 一个可独立交付的“智能产品”的开发环境"""
    __tablename__ = 'ai_projects'
    # 必要
    id = Column(Integer, primary_key=True, comment="项目唯一主键ID")
    # 必要
    uuid = Column(String(36), nullable=False, unique=True, index=True, default=generate_uuid, comment="项目的全局唯一标识符")
    # 必要
    workspace_id = Column(Integer, ForeignKey('ai_workspaces.id', ondelete='CASCADE'), nullable=False, index=True, comment="所属工作空间ID")
    # 必要
    creator_id = Column(Integer, ForeignKey('users.id'), nullable=False, comment="项目创建者的用户ID")
    # 必要
    name = Column(String(255), nullable=False, comment="项目名称")
    # 增强/QoL
    description = Column(Text, nullable=True, comment="项目描述")
    # 增强/QoL
    avatar = Column(String(512), nullable=True, comment="项目图标URL")
    # 项目级环境配置 (可选上下文)
    env_config = Column(JSON, nullable=False, default={}, comment="项目级环境配置 (运行时可选注入)")
    # 必要
    main_resource_id = Column(Integer, ForeignKey('ai_resources.id', use_alter=True, ondelete='SET NULL'), nullable=True, comment="项目的主资源/入口资源ID")
    # 必要
    visibility = Column(Enum(ProjectVisibility), nullable=False, default=ProjectVisibility.PRIVATE, comment="项目可见性 (私有, 工作空间, 公开)")
    # 必要
    status = Column(Enum(ProjectStatus), nullable=False, default=ProjectStatus.DRAFT, comment="项目状态 (草稿, 活跃, 归档, 模板)")
    # 必要
    created_at = Column(DateTime, nullable=False, server_default=func.now(), comment="项目创建时间")
    # 必要
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now(), comment="项目信息最后更新时间")

    workspace = relationship("Workspace", back_populates="projects", lazy="joined")
    creator = relationship("User", lazy="joined")
    resource_refs = relationship("ProjectResourceRef", back_populates="project", cascade="all, delete-orphan")
    main_resource = relationship("Resource", foreign_keys=[main_resource_id])
