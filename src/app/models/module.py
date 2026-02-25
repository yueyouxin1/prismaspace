import enum
from sqlalchemy import (
    Column, Integer, String, Text, JSON, Boolean, Enum, ForeignKey,
    DateTime, func, Index, DECIMAL, UniqueConstraint, CheckConstraint
)
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.utils.id_generator import generate_uuid

class ServiceModuleStatus(enum.Enum):
    AVAILABLE = "available"
    BETA = "beta"
    DEPRECATED = "deprecated"
    UNAVAILABLE = "unavailable"

class ServiceModuleType(Base):
    """服务模块类型表 - 能力的顶层分类，用于发现和组织。"""
    __tablename__ = 'service_module_types'
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False, comment="类型的唯一标识符 (e.g., 'llm', 'tts', 'image_generation', 'code_interpreter')")
    label = Column(String(100), nullable=False, comment="UI上显示的名称 (e.g., '大型语言模型', '语音合成')")
    description = Column(Text, nullable=True)
    default_version_id = Column(Integer, ForeignKey('service_module_versions.id', use_alter=True, ondelete='SET NULL'), nullable=True)
    service_modules = relationship("ServiceModule", back_populates="type")
    default_version = relationship("ServiceModuleVersion", foreign_keys=[default_version_id], post_update=True)
    
class ServiceModuleProvider(Base):
    """[NEW] 权威的服务提供商字典表"""
    __tablename__ = 'service_module_providers'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False, comment="提供商的唯一标识符, e.g., 'openai', 'aliyun'")
    label = Column(String(255), nullable=False, comment="UI上显示的名称, e.g., 'OpenAI', '阿里云通义'")
    description = Column(Text, nullable=True)

    service_modules = relationship("ServiceModule", back_populates="provider")

class ServiceModuleDependency(Base):
    """
    服务模块依赖关系表 - 记录服务模块版本之间静态的、设计时的依赖关系。
    这是一个纯粹的依赖记录，与运行时无关。
    """
    __tablename__ = 'service_module_dependencies'
    
    # [关键] 哪个服务版本 (依赖方)
    dependant_version_id = Column(Integer, ForeignKey('service_module_versions.id', ondelete='CASCADE'), primary_key=True, comment="依赖方 (Dependant) 的服务模块版本ID")
    
    # [关键] 依赖于哪个服务版本 (被依赖方)
    dependency_version_id = Column(Integer, ForeignKey('service_module_versions.id', ondelete='CASCADE'), primary_key=True, comment="被依赖方 (Dependency) 的服务模块版本ID")
    
    # [增强/未来]
    is_hard_dependency = Column(Boolean, default=True, comment="是否是硬依赖。若为False，表示为可选或推荐依赖。")
    
    # --- SQLAlchemy 关系定义 ---
    # 定义从这张关联表到 ServiceModuleVersion 的关系
    dependant_version = relationship("ServiceModuleVersion", foreign_keys=[dependant_version_id], back_populates="dependencies")
    dependency_version = relationship("ServiceModuleVersion", foreign_keys=[dependency_version_id], back_populates="dependants")

class ServiceModuleCredential(Base):
    """
    [Domain Model] 服务模块凭证表。
    存储用户或团队为特定的 ServiceModule provider 提供的加密凭证。
    """
    __tablename__ = 'service_module_credentials'

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True, nullable=False)
    
    # [关键] 这里的 name 对应 ServiceModule.provider (e.g., 'openai', 'anthropic')
    # 我们不直接关联 service_module_id，因为一个凭证通常通用于该 provider 下的所有模型
    provider_id = Column(Integer, ForeignKey('service_module_providers.id'), nullable=False, index=True)
    
    # 友好的显示名称，方便用户管理
    label = Column(String(255), nullable=True, comment="e.g., 'My Company OpenAI Key'")
    
    # [安全] 存储加密后的 API Key
    encrypted_value = Column(Text, nullable=False)
    encrypted_endpoint = Column(Text, nullable=True, comment="[可选] 加密后的API端点URL")
    region = Column(String(100), nullable=True, comment="[可选] 服务区域")
    attributes = Column(JSON, nullable=True, comment="[可选] 其他提供商特定的、非敏感的元数据")
    
    workspace_id = Column(Integer, ForeignKey('ai_workspaces.id', ondelete='CASCADE'), nullable=False, index=True)

    # 审计字段
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    provider = relationship("ServiceModuleProvider")
    workspace = relationship("Workspace")

    __table_args__ = (
        UniqueConstraint('workspace_id', 'provider_id', name='uq_workspace_provider_credential'),
    )

class ServiceModule(Base):
    """
    服务模块表 - 平台所有内置“能力”的逻辑定义。
    它是一个“抽象类”，具体的版本在 ServiceModuleVersion 中定义。
    """
    __tablename__ = 'service_modules'
    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True, nullable=False)
    # [必要]
    type_id = Column(Integer, ForeignKey('service_module_types.id'), nullable=False)
    name = Column(String(100), unique=True, nullable=False, comment="服务模块的唯一名称 (e.g., 'gpt-4-turbo', 'deepgram-nova-2')")
    label = Column(String(255), nullable=False, comment="UI上显示的名称")
    description = Column(Text, nullable=True)
    provider_id = Column(Integer, ForeignKey('service_module_providers.id'), nullable=False, index=True)
    requires_credential = Column(Boolean, nullable=False, default=False, comment="是否必须提供用户凭证才能执行")
    # [关键] 指针指向最新的/推荐的版本
    latest_version_id = Column(Integer, ForeignKey('service_module_versions.id', use_alter=True, ondelete='SET NULL'), nullable=True)
    permission_id = Column(Integer, ForeignKey('action_permissions.id', ondelete='CASCADE'), nullable=False, unique=True)

    type = relationship("ServiceModuleType", back_populates="service_modules")
    provider = relationship("ServiceModuleProvider", back_populates="service_modules", lazy="joined")
    latest_version = relationship("ServiceModuleVersion", foreign_keys=[latest_version_id])
    versions = relationship("ServiceModuleVersion", back_populates="service_module", cascade="all, delete-orphan", foreign_keys="ServiceModuleVersion.service_module_id")
    permission = relationship("ActionPermission", lazy="joined", uselist=False)

class ServiceModuleVersion(Base):
    """
    服务模块版本表 - 一个具体、可调用的服务模块实例。
    这是用户在构建时真正选择和使用的实体。
    """
    __tablename__ = 'service_module_versions'
    
    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True, nullable=False)
    # [必要]
    service_module_id = Column(Integer, ForeignKey('service_modules.id', ondelete='CASCADE'), nullable=False, index=True)
    # 必要
    name = Column(String(100), unique=True, nullable=False, comment="此版本的唯一名称 (e.g., 'qwen-plus-latest', 'gpt-4-turbo-2024-04-09')")
    # 增强/QoL
    description = Column(Text, nullable=True, comment="此版本的描述")
    version_tag = Column(String(100), nullable=False, comment="版本的唯一标识符 (e.g., '2024-04-09', 'v2.1.0')")
    version_notes = Column(Text, comment="版本更新说明")
    # [关键] 状态与可用性
    status = Column(Enum(ServiceModuleStatus), nullable=False, default=ServiceModuleStatus.AVAILABLE, index=True)
    availability_regions = Column(JSON, nullable=True, comment="[可选] 可用区域列表 (e.g., ['us-east-1', 'eu-west-1'])")
    
    # [关键] 配置与约束
    config = Column(JSON, nullable=True, comment="[Pydantic-driven] The default configuration object, serialized from the authoritative Pydantic model.")

    # 存储固有规格对象
    attributes = Column(JSON, nullable=True, comment="[Pydantic-driven] The immutable specifications object, serialized from the authoritative Pydantic model.")

    # [审计/安全]
    release_date = Column(DateTime)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    
    service_module = relationship(
        "ServiceModule", 
        back_populates="versions",
        lazy="joined",
        foreign_keys=[service_module_id] # <--- 明确指定外键
    )
    
    # [关键新增] “我依赖谁？” - A list of ServiceModuleDependency objects where this version is the dependant.
    # 通过这个关系，我们可以轻松地找到一个版本的所有“下游”依赖。
    dependencies = relationship("ServiceModuleDependency",
        foreign_keys=[ServiceModuleDependency.dependant_version_id],
        back_populates="dependant_version",
        cascade="all, delete-orphan"
    )
    
    # [关键新增] “谁依赖我？” - A list of ServiceModuleDependency objects where this version is the dependency.
    # 通过这个关系，我们可以轻松地找到一个版本的所有“上游”使用者，这是进行变更影响分析的核心。
    dependants = relationship("ServiceModuleDependency",
        foreign_keys=[ServiceModuleDependency.dependency_version_id],
        back_populates="dependency_version",
        cascade="all, delete-orphan"
    )
    
    features = relationship("Feature", back_populates="service_module_version", cascade="all, delete-orphan", lazy="joined")

    __table_args__ = (
        UniqueConstraint('service_module_id', 'version_tag', name='uq_service_module_version'),
    )