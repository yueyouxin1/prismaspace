import enum
from decimal import Decimal
from sqlalchemy import (
    Column, Integer, String, Text, JSON, Boolean, Enum, ForeignKey,
    DateTime, func, Index, DECIMAL, UniqueConstraint, CheckConstraint, PrimaryKeyConstraint 
)
from sqlalchemy.orm import relationship
from app.db.base import Base

# 定义 Trace 的生命周期状态
class TraceStatus(enum.Enum):
    PENDING = "pending"      # 待处理
    PROCESSED = "processed"  # 处理完成
    FAILED = "failed"        # 处理失败
    CANCELLED = "cancelled"  # 已中止

class ActivityLog(Base):
    """
    用户行为日志表 - 记录用户在平台上的所有“有意义”的操作(定义于 action_permissions 表)。
    通过parent_id构建行为树，将完整周期行为关联起来。
    """
    __tablename__ = 'activity_logs'
    
    # [必要]
    id = Column(Integer, primary_key=True, comment="日志唯一主键ID")
    
    # [关键设计] 用于构建“页面-交互”树状结构
    # [必要]
    parent_id = Column(Integer, ForeignKey('activity_logs.id', ondelete='CASCADE'), nullable=True, index=True, comment="父行为ID。用于将交互事件关联到其所属的页面浏览事件。")

    # --- 行为主体 (Actor) ---
    # [必要]
    actor_user_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True, comment="执行操作的用户ID")
    # [增强/审计]
    actor_team_id = Column(Integer, ForeignKey('teams.id', ondelete='SET NULL'), nullable=True, index=True, comment="用户执行操作时所在的团队上下文ID")
    # [增强/审计]
    actor_ip_address = Column(String(45), nullable=True, comment="操作者的IP地址")
    
    # --- 行为动作 (Action) ---
    # [关键] 直接、唯一地关联到 action_permissions 表
    action_id = Column(Integer, ForeignKey('action_permissions.id'), nullable=False, index=True, comment="执行的动作ID，关联到权威的action_permissions表")
    
    # --- [可选] 权威快照 ---
    # [审计/安全]
    acting_role_id = Column(Integer, ForeignKey('roles.id', ondelete='SET NULL'), nullable=True, comment="[快照指针] 执行此操作时，用户所扮演的角色的ID")
    # [审计/安全]
    acting_role_name = Column(String(100), nullable=True, comment="[快照值] 执行此操作时，角色的名称（冗余，用于快速审计）")
    # [审计/安全]
    target_action_name = Column(String(100), nullable=True, comment="[快照值] 此操作目标动作权限名称")
    
    # --- 行为结果与上下文 ---
    # [增强/调试]
    status_code = Column(Integer, nullable=True, comment="操作的结果状态码 (e.g., 200 for success, 403 for forbidden)")
    # [必要]
    context = Column(JSON, nullable=True, comment="与行为相关的附加上下文信息 (e.g., 'page_url': '/projects/123/edit', 'search_term': 'weather')")
    
    # [必要]
    timestamp = Column(DateTime, nullable=False, server_default=func.now(), index=True, comment="行为发生的时间戳")

    # --- 关系定义 ---
    parent = relationship("ActivityLog", remote_side=[id], back_populates="children")
    children = relationship("ActivityLog", back_populates="parent", cascade="all, delete-orphan")
    action = relationship("ActionPermission")
    user = relationship("User", back_populates="activity_logs")
    team = relationship("Team", back_populates="activity_logs")

class Trace(Base):
    """
    追踪与用量表 - 平台可观测性与计费的核心。
    每一行代表一个“跨度(Span)”，共同构成一次完整的“追踪(Trace)”。
    """
    __tablename__ = 'traces'
    id = Column(Integer, primary_key=True, comment="物理自增主键，用于聚集索引和分页")
    span_uuid = Column(String(36), unique=True, nullable=False, index=True, comment="Span的逻辑唯一ID (UUID)")
    trace_id = Column(String(36), nullable=False, index=True, comment="全链路追踪ID，贯穿整个请求周期")
    
    # [关键] 这里的外键指向的是 span_uuid (逻辑键)，而不是 id (物理键)
    # 这允许我们在不获取物理 ID 的情况下，在内存中构建父子关系并批量插入
    parent_span_uuid = Column(String(36), ForeignKey('traces.span_uuid', use_alter=True), nullable=True, index=True, comment="父Span的逻辑UUID")
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True, comment="发起调用的终端用户ID")
    # 增强/审计
    api_key_id = Column(Integer, ForeignKey('api_keys.id'), nullable=True, index=True, comment="[可选]用于本次调用的API密钥ID")

    # 2. 执行/调试上下文
    # Source: 谁发起的？
    source_instance_id = Column(Integer, ForeignKey('ai_resource_instances.id'), nullable=True, index=True)
    # Target: 谁被执行？
    target_instance_id = Column(Integer, ForeignKey('ai_resource_instances.id'), nullable=True, index=True)

    # --- [关键] 结构化与非结构化信息分离 ---
    # [必要] 结构化、可索引的核心信息
    operation_name = Column(String(255), nullable=False, index=True, comment="操作的名称，用于分类和聚合 (e.g., 'user.input', 'llm.completion', 'tool.call', 'workflow.call)")
    
    context_type = Column(String(50), nullable=True, index=True, comment="业务容器类型")
    context_id = Column(String(36), nullable=True, index=True, comment="业务容器ID")

    attributes = Column(JSON, nullable=True, comment="结构化的调试数据快照 (Inputs, Outputs, Meta)")

    # 观察
    status = Column(Enum(TraceStatus), nullable=False, default=TraceStatus.PENDING, index=True)
    duration_ms = Column(Integer, comment="调用持续时间(毫秒)")
    # 增强/调试
    error_message = Column(Text, nullable=True, comment="如果发生错误，记录错误信息")
    # 必要
    created_at = Column(DateTime, nullable=False, server_default=func.now(), index=True, comment="操作开始的时间戳")
    processed_at = Column(DateTime, nullable=True, index=True, comment="操作完成的时间戳")
    # --- 关系定义 ---
    # 用于 ConsumptionService 加载
    user = relationship("User")
    parent = relationship(
        "Trace",
        remote_side=[span_uuid], 
        back_populates="children",
        foreign_keys=[parent_span_uuid]
    )
    
    children = relationship(
        "Trace",
        back_populates="parent",
        cascade="all, delete-orphan",
        foreign_keys=[parent_span_uuid]
    )
    
    # 指向 ConsumptionRecord 的关系 (一对一)
    # ConsumptionRecord.trace_span_id 应该是指向 Trace.id 还是 Trace.span_uuid?
    # 既然 ConsumptionRecord 是事后结算，且 Trace 已经落库，使用物理 ID (Trace.id) 关联效率更高。
    consumption_record = relationship(
        "ConsumptionRecord", 
        back_populates="trace",
        uselist=False
    )