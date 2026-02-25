# src/app/models/resource/base.py

import enum
from sqlalchemy import (
    Column, Integer, String, Text, JSON, Boolean, Enum, ForeignKey,
    DateTime, func, Index, DECIMAL, UniqueConstraint, CheckConstraint
)
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.utils.id_generator import generate_uuid

# 我们的自动化注册中心
ALL_INSTANCE_TYPES = []

class ResourceStatus(enum.Enum): ACTIVE = "active"; SUSPENDED = "suspended"; ARCHIVED = "archived"

class VersionStatus(enum.Enum):
    WORKSPACE = "workspace"  # 永远只有一个，是活跃的草稿
    DRAFT = "draft"          # 用户手动保存的草稿
    PUBLISHED = "published"  # 已发布到线上
    ARCHIVED = "archived"    # 已归档/下线
    PENDING_APPROVAL = "pending_approval" # 已提交，等待审核

class AuthType(enum.Enum): NONE = "none"; API_KEY = "api_key"

class ResourceType(Base):
    """资源类型表 - 系统的权威字典，定义了有哪些类型的资源"""
    __tablename__ = 'ai_resource_types'
    # 必要
    id = Column(Integer, primary_key=True, comment="资源类型唯一主键ID")
    # 必要
    name = Column(String(50), nullable=False, unique=True, comment="类型的唯一标识符 (e.g., 'uiapp', 'tool')")
    # 增强/QoL
    label = Column(String(100), comment="UI友好名称 (e.g., '可视化应用')")
    description = Column(Text, nullable=True)
    # 必要
    is_application = Column(Boolean, nullable=False, default=False, comment="是否可作为项目的主应用/入口资源")
    # 这是一个JSON字段，用于存储该类型资源的所有平台级默认行为和约束
    meta_policy = Column(JSON, nullable=True, comment="[补充性] 定义该类型资源行为的元策略")
    # meta_policy 示例:
    # {
    #   "versioning": {
    #     "max_versions_per_resource": 100 // 该类型资源最多保留多少个历史版本
    #   },
    #   "capabilities": {
    #     "can_be_favorited": true,
    #     "can_be_commented": true
    #   }
    # }
    allowed_visibilities = Column(JSON, nullable=False, default=["public", "workspace", "private"], comment="允许的可见性选项")
    allowed_channels = Column(JSON, nullable=False, default=["default", "marketplace", "api"], comment="允许的发布渠道")
    requires_approval = Column(Boolean, nullable=False, default=False, comment="是否需要平台审核")

class ResourceCategory(Base):
    """资源分类表 - 用于资源的发现和组织"""
    __tablename__ = 'ai_resource_categories'
    # 必要
    id = Column(Integer, primary_key=True, comment="分类唯一主键ID")
    # 必要
    name = Column(String(100), nullable=False, unique=True, comment="分类名称")
    # 增强/未来
    parent_id = Column(Integer, ForeignKey('ai_resource_categories.id'), nullable=True, comment="父分类ID，用于实现层级分类")

class Resource(Base):
    """资源主表 - 平台所有创作单元的“身份卡”"""
    __tablename__ = 'ai_resources'
    # 必要
    id = Column(Integer, primary_key=True, comment="资源唯一主键ID")
    # 必要
    uuid = Column(String(36), nullable=False, unique=True, index=True, default=generate_uuid, comment="资源的全局唯一标识符")
    # 必要
    workspace_id = Column(Integer, ForeignKey('ai_workspaces.id', ondelete='CASCADE'), nullable=False, index=True, comment="所属工作空间ID")
    # [关键回归] 权威的、有外键约束的类型ID
    resource_type_id = Column(Integer, ForeignKey('ai_resource_types.id'), nullable=False, index=True, comment="资源类型ID")
    # 运营/增长
    category_id = Column(Integer, ForeignKey('ai_resource_categories.id'), nullable=True, index=True, comment="资源分类ID")
    # 这是用户在列表中看到的、最新的、可独立编辑、与版本无关的“门面”元数据
    name = Column(String(255), nullable=False, comment="资源的当前名称")
    description = Column(Text, nullable=True, comment="资源的当前描述")
    avatar = Column(String(512), nullable=True, comment="资源图标URL")
    # 平台级的资源总开关
    status = Column(Enum(ResourceStatus), nullable=False, default=ResourceStatus.ACTIVE, comment="平台级的资源总开关 (活跃, 暂停, 归档)")
    # 运营/增长
    heat_value = Column(Integer, nullable=False, default=0, index=True, comment="热度值，用于排序和推荐")
    # [关键] 指针指向特定的版本，以定义“工作区”和“线上”是哪个版本
    workspace_instance_id = Column(Integer, ForeignKey('ai_resource_instances.id', use_alter=True, ondelete='SET NULL'), nullable=True)
    latest_published_instance_id = Column(Integer, ForeignKey('ai_resource_instances.id', use_alter=True, ondelete='SET NULL'), nullable=True)
    # 必要
    creator_id = Column(Integer, ForeignKey('users.id'), nullable=False, comment="资源创建者的用户ID")
    # 增强/审计
    last_modifier_id = Column(Integer, ForeignKey('users.id'), nullable=True, comment="最后修改者的用户ID")
    # 必要
    created_at = Column(DateTime, nullable=False, server_default=func.now(), comment="资源创建时间")
    # 必要
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now(), comment="资源最后更新时间")

    # 默认按需加载，热路径由 DAO 显式声明 eager 策略。
    resource_type = relationship("ResourceType")
    creator = relationship("User", foreign_keys=[creator_id])
    workspace = relationship("Workspace", back_populates="resources")
    project_refs = relationship("ProjectResourceRef", back_populates="resource", cascade="all, delete-orphan")

    # 按需关系
    category = relationship("ResourceCategory")
    last_modifier = relationship("User", foreign_keys=[last_modifier_id])
    workspace_instance = relationship("ResourceInstance", foreign_keys=[workspace_instance_id], uselist=False, post_update=True)
    latest_published_instance = relationship("ResourceInstance", foreign_keys=[latest_published_instance_id], uselist=False, post_update=True)
    instance_versions = relationship(
        "ResourceInstance", 
        back_populates="resource", 
        cascade="all, delete-orphan",
        # 明确告诉SQLAlchemy，这个关系是通过 ResourceInstance 表中的 'resource_id' 字段建立的
        foreign_keys="ResourceInstance.resource_id" 
    )


class ResourceRef(Base):
    """
    资源引用表 - 建立不同资源版本之间的精确依赖关系图。
    它代表了有向图中的一条“边”。
    """
    __tablename__ = 'ai_resource_refs'
    
    # 必要
    id = Column(Integer, primary_key=True, comment="引用关系唯一主键ID")
    
    # [关键修正] 移除 project_id，引用关系的上下文由源头决定
    # project_id = Column(Integer, ForeignKey('ai_projects.id'), ..., nullable=False) # <--- DELETED

    # --- 边的起点 (Source of the Edge) ---
    # 必要
    source_resource_id = Column(Integer, ForeignKey('ai_resources.id', ondelete='CASCADE'), nullable=False, index=True, comment="发起引用的源逻辑资源ID")
    # 必要
    source_instance_id = Column(Integer, ForeignKey('ai_resource_instances.id', ondelete='CASCADE'), nullable=False, index=True, comment="发起引用的源资源具体版本ID")

    source_node_uuid = Column(String(64), nullable=False, default="", index=True, comment="源资源内部的局部节点/组件标识符")

    # --- 边的终点 (Target of the Edge) ---
    # 必要
    target_resource_id = Column(Integer, ForeignKey('ai_resources.id', ondelete='CASCADE'), nullable=False, index=True, comment="被引用的目标逻辑资源ID")
    # 必要
    target_instance_id = Column(Integer, ForeignKey('ai_resource_instances.id', ondelete='CASCADE'), nullable=False, index=True, comment="被引用的目标资源具体版本ID (版本锁定)")
    
    # --- 边的属性 (Attributes of the Edge) ---
    # 增强/QoL
    alias = Column(String(255), nullable=True, comment="在上下文中为该引用设置的别名，便于UI展示")
    # 增强/额外配置
    options = Column(JSON, nullable=True, comment="引用携带的配置")
    
    # --- SQLAlchemy 关系定义 ---
    # [关键修正] 移除了与 project 的直接关系
    # project = relationship("Project") # <--- DELETED
    
    source_resource = relationship("Resource", foreign_keys=[source_resource_id])
    source_instance = relationship("ResourceInstance", foreign_keys=[source_instance_id], back_populates="source_refs")
    
    target_resource = relationship("Resource", foreign_keys=[target_resource_id])
    target_instance = relationship("ResourceInstance", foreign_keys=[target_instance_id], back_populates="target_refs")

    # --- 引用属性 ---
    alias = Column(String(255), nullable=True, comment="别名，用于代码中引用 (e.g. 'weather_tool')")
    
    __table_args__ = (
        # 唯一性约束：同一个源节点的同一个实例，对同一个目标实例只能有一条引用
        UniqueConstraint('source_instance_id', 'source_node_uuid', 'target_instance_id', name='uq_ref_edge'),
    )

class ProjectResourceRef(Base):
    """
    项目资源引用表 - 表达项目与资源之间的直接依赖关系。
    """
    __tablename__ = 'ai_project_resource_refs'

    id = Column(Integer, primary_key=True, comment="项目资源引用唯一主键ID")
    project_id = Column(Integer, ForeignKey('ai_projects.id', ondelete='CASCADE'), nullable=False, index=True, comment="项目ID")
    resource_id = Column(Integer, ForeignKey('ai_resources.id', ondelete='CASCADE'), nullable=False, index=True, comment="资源ID")
    alias = Column(String(255), nullable=True, comment="项目内别名")
    options = Column(JSON, nullable=True, comment="项目级引用配置")
    created_at = Column(DateTime, nullable=False, server_default=func.now(), comment="引用创建时间")

    project = relationship("Project", back_populates="resource_refs")
    resource = relationship("Resource", back_populates="project_refs")

    __table_args__ = (
        UniqueConstraint('project_id', 'resource_id', name='uq_project_resource'),
    )

class ApiPolicy(Base):
    __tablename__ = 'ai_api_policies'
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    workspace_id = Column(Integer, ForeignKey('ai_workspaces.id', ondelete='CASCADE'), nullable=False)
    
    auth_type = Column(Enum(AuthType), nullable=False, default=AuthType.API_KEY)
    rate_limit_per_minute = Column(Integer, nullable=True)

class ResourceInstance(Base):
    """
    版本实体表 - 赋予“实现快照”生命周期、元数据和意义。
    这是所有版本管理的唯一入口。
    """
    __tablename__ = 'ai_resource_instances'
    
    # 必要
    id = Column(Integer, primary_key=True, comment="版本实体的唯一ID (version_id)")
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True, nullable=False)
    # 父版本实例ID，用于构建版本树
    parent_version_id = Column(Integer, ForeignKey('ai_resource_instances.id', ondelete='SET NULL'), nullable=True, index=True)
    # 必要
    resource_id = Column(Integer, ForeignKey('ai_resources.id', ondelete='CASCADE'), nullable=False, index=True, comment="关联的资源ID")
    # [关键] 多态鉴别器
    resource_type = Column(String(50), nullable=False, comment="资源类型, 其值来源于 Resource.resource_type.name")
    # --- 版本元数据 (Version Metadata) ---
    # 必要 (从实现表移入)
    version_tag = Column(String(50), nullable=False, index=True, comment="版本标签 (e.g., '__workspace__', '1.0.0', 'latest')")
    # 必要 (新增)
    name = Column(String(255), nullable=False, comment="此版本的名称")
    # 增强/QoL
    description = Column(Text, nullable=True, comment="此版本的描述")
    # 增强/QoL (从实现表移入)
    version_notes = Column(Text, comment="版本更新说明")
    
    # --- 生命周期 (Lifecycle) ---
    # 必要 (从实现表移入)
    status = Column(Enum(VersionStatus), nullable=False, default=VersionStatus.DRAFT, index=True, comment="版本状态")
    # --- 发布策略 (Publishing Strategy) ---
    # 之前在ResourcePublication中的所有策略字段都在这里
    # 必要
    visibility = Column(String(50), default='private', comment="发布时的可见范围, 其值来源于 Resource.resource_type.allowed_visibilities")
    # 增强/未来
    channel = Column(String(50), default='default', comment="发布渠道, 其值来源于 Resource.resource_type.allowed_channels")
    # 增强/未来
    pricing_model = Column(JSON, nullable=True, comment="定价模型 (e.g., {'type': 'per_call', 'price': 0.001})")
    # 必要
    api_policy_id = Column(Integer, ForeignKey('ai_api_policies.id'), nullable=True, comment="本次发布应用的API策略ID")
    # 指向一个“计费特性”
    # 如果此字段有值，代表这个资源实例是一个“官方收费资源”
    linked_feature_id = Column(Integer, ForeignKey('features.id'), nullable=True, index=True)
    # --- 审计 (Audit) ---
    # 必要
    creator_id = Column(Integer, ForeignKey('users.id'), nullable=False, comment="版本创建者的用户ID")
    # 增强/审计
    published_by = Column(Integer, ForeignKey('users.id'), nullable=True, comment="执行发布操作的用户ID")
    # 增强/审计
    approver_id = Column(Integer, ForeignKey('users.id'), nullable=True, comment="审核通过的用户ID")
    # 必要
    created_at = Column(DateTime, nullable=False, server_default=func.now(), comment="版本创建时间")
    # 增强/审计
    published_at = Column(DateTime, nullable=True, comment="版本发布生效时间")

    # 默认按需加载，避免在多态基表查询时隐式扩大 JOIN 范围。
    resource = relationship(
        "Resource", 
        back_populates="instance_versions",
        foreign_keys=[resource_id]
    )
    creator = relationship(
        "User", 
        foreign_keys=[creator_id]
    )
    linked_feature = relationship("Feature")

    # 按需关系
    published_user = relationship("User", foreign_keys=[published_by])
    approver = relationship("User", foreign_keys=[approver_id])
    # 我作为源头发出的所有引用
    source_refs = relationship("ResourceRef", foreign_keys=[ResourceRef.source_instance_id], back_populates="source_instance", cascade="all, delete-orphan")
    
    # 我作为目标被引用的所有关系
    target_refs = relationship("ResourceRef", foreign_keys=[ResourceRef.target_instance_id], back_populates="target_instance", cascade="all, delete-orphan")
    
    __table_args__ = (
        UniqueConstraint('resource_id', 'version_tag', name='uq_resource_version_tag'),
        # 版本列表热路径: WHERE resource_id = ? ORDER BY created_at DESC
        Index('ix_resource_instances_resource_created_at', 'resource_id', 'created_at'),
        # 一个资源只能有一个 workspace 状态的版本
        Index('ix_resource_workspace_status', 'resource_id', unique=True, postgresql_where=(status == VersionStatus.WORKSPACE)),
    )
    __mapper_args__ = {
        'polymorphic_on': resource_type,
        'polymorphic_identity': 'base_instance'
    }
