# src/app/models/interaction/chat.py

from sqlalchemy import Column, Integer, String, Text, JSON, Enum, ForeignKey, DateTime, func, Boolean
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.utils.id_generator import generate_uuid
import enum

class MessageRole(str, enum.Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"

class ChatSession(Base):
    """
    会话表 - 代表 Agent 与 用户的一次连续交互上下文。
    """
    __tablename__ = 'ai_chat_sessions'

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True, nullable=False)
    
    # 归属
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    
    # [关键] 绑定特定的 Agent Instance
    agent_instance_id = Column(Integer, ForeignKey('ai_resource_instances.id'), nullable=False, index=True)
    
    title = Column(String(255), nullable=True, comment="会话标题 (自动生成或用户设定)")
    summary = Column(Text, nullable=True, comment="会话摘要")

    # 性能数据
    message_count = Column(Integer, default=0, nullable=False, comment="正常消息数量")

    # 状态
    is_archived = Column(Boolean, default=False, index=True, comment="会话是否已归档(软删除)")
    
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    # 关系
    user = relationship("User")
    agent_instance = relationship("ResourceInstance")
    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.id")

class ChatMessage(Base):
    """
    消息表 - 记录每一条交互内容。
    """
    __tablename__ = 'ai_chat_messages'

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True, nullable=False)
    
    session_id = Column(Integer, ForeignKey('ai_chat_sessions.id', ondelete='CASCADE'), nullable=False, index=True)
    
    # 核心内容
    role = Column(Enum(MessageRole), nullable=False)
    content = Column(Text, nullable=True) # Tool Call 的 content 可能为空
    
    # [增强] 多模态与元数据支持 (对齐原型 media 字段)
    # 结构: { "images": ["url..."], "files": [{"name": "x.pdf", "id": "..."}], "client_info": {...} }
    meta = Column(JSON, nullable=True, default={})

    # Tool Call 专用
    tool_calls = Column(JSON, nullable=True) 
    tool_call_id = Column(String(100), nullable=True)
    
    # 性能数据
    token_count = Column(Integer, default=0, comment="估算的 Token 数量")
    
    # 溯源
    trace_id = Column(String(36), nullable=True, index=True, comment="关联的 Trace ID")
    
    # [关键新增] 消息级软删除，用于生产环境“清空上下文”
    is_deleted = Column(Boolean, default=False, index=True)
    
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    session = relationship("ChatSession", back_populates="messages")