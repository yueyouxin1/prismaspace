import enum
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, Enum, ForeignKey,
    DateTime, func, UniqueConstraint
)
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.utils.id_generator import generate_uuid

class MemoryScope(str, enum.Enum):
    USER = "user"       # 长期记忆：跟随用户，跨会话持久
    SESSION = "session" # 短期记忆：跟随会话，会话结束后虽然仍在库中但逻辑上隔离

class MemoryType(str, enum.Enum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    LIST = "list"
    OBJECT = "object"

class AgentMemoryVar(Base):
    """
    [定义表 - Schema]
    定义 Agent 拥有哪些记忆变量。这是设计态数据。
    """
    __tablename__ = 'ai_agent_memories'
    
    id = Column(Integer, primary_key=True)
    # 归属 Agent 版本
    agent_id = Column(Integer, ForeignKey('ai_agents.version_id', ondelete='CASCADE'), nullable=False, index=True)
    
    # 变量定义
    key = Column(String(64), nullable=False, comment="变量名 (英文标识符)")
    label = Column(String(64), nullable=False, comment="显示名称")
    type = Column(Enum(MemoryType), default=MemoryType.STRING, nullable=False, comment="变量类型")
    description = Column(Text, nullable=True, comment="语义描述，帮助 LLM 理解该变量的用途")
    
    # 行为控制
    default_value = Column(Text, nullable=True, comment="默认值 (序列化后的字符串)")
    scope_type = Column(Enum(MemoryScope), default=MemoryScope.USER, nullable=False, comment="记忆的作用范围")
    is_active = Column(Boolean, default=True, comment="是否启用")
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('agent_id', 'key', name='uq_agent_memory_key'),
    )

class AgentMemoryVarValue(Base):
    """
    [值表 - Runtime State]
    存储具体的运行时数据。这是运行态数据，量大且频变。
    """
    __tablename__ = 'ai_agent_memory_values'
    
    id = Column(Integer, primary_key=True)
    
    # 关联到定义
    memory_id = Column(Integer, ForeignKey('ai_agent_memories.id', ondelete='CASCADE'), nullable=False, index=True)
    
    # 作用域锚点 (User ID 或 Session UUID)
    # 如果 scope_type=USER，则 user_id 必填
    # 如果 scope_type=SESSION，则 session_id 必填 (通常存 session_uuid 字符串以解耦)
    user_id = Column(Integer, nullable=True, index=True) 
    session_uuid = Column(String(36), nullable=True, index=True)
    
    # 实际值 (存储为字符串，读取时根据 AgentMemoryVar.type 转换)
    value = Column(Text, nullable=True)
    
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    memory = relationship("AgentMemoryVar")

    __table_args__ = (
        # 索引优化：根据查询模式优化
        # 场景1: 查找某用户的所有记忆 -> (user_id, memory_id)
        # 场景2: 查找某会话的所有记忆 -> (session_uuid, memory_id)
    )

class SummaryScope(str, enum.Enum):
    USER = "user"       # 用户级摘要：跟随用户，跨会话生效（例如用户偏好）
    SESSION = "session" # 会话级摘要：仅当前会话有效（例如当前任务上下文）

class AgentContextSummary(Base):
    """
    [深度记忆 Layer 2] 上下文摘要表。
    存储由后台任务生成的、针对特定对话轮次的语义摘要。
    """
    __tablename__ = 'ai_agent_context_summaries'
    
    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True)
    
    # 归属
    agent_instance_id = Column(Integer, ForeignKey('ai_resource_instances.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    
    # 作用域控制
    scope = Column(Enum(SummaryScope), default=SummaryScope.SESSION, nullable=False, index=True)
    session_uuid = Column(String(36), nullable=True, index=True, comment="如果是 Session 级，必须绑定 Session UUID")
    
    # 来源溯源 (Traceability)
    # 记录该摘要是基于哪一轮（或哪几轮）生成的。通常对应触发摘要任务的那个 Trace ID。
    trace_id = Column(String(36), nullable=False, index=True, comment="该摘要对应的原始对话轮次Trace ID")
    
    # [关键新增] 记录生成此摘要所使用的 LLM 模型版本
    # 用于成本归因、审计以及未来可能的重新生成策略
    module_version_id = Column(Integer, ForeignKey('service_module_versions.id', ondelete='SET NULL'), nullable=True)
    
    # 内容
    content = Column(Text, nullable=False, comment="LLM生成的摘要内容")
    
    # 状态
    is_archived = Column(Boolean, default=False, index=True, comment="是否已归档（不再被检索到，但保留记录）")

    # [新增] 引用时间：记录该摘要所对应的原始 Trace (第一条消息) 的创建时间
    # 这才是排序的唯一真理，而不是摘要生成的 created_at
    ref_created_at = Column(DateTime, index=True, nullable=True, comment="原始对话轮次的发生时间")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # 关系
    agent_instance = relationship("ResourceInstance")
    module_version = relationship("ServiceModuleVersion")