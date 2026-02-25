# src/app/schemas/interaction/chat_schemas.py

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime
from app.models.interaction.chat import MessageRole

# --- Session Schemas ---

class ChatSessionCreate(BaseModel):
    agent_instance_uuid: str = Field(..., description="要交互的 Agent 实例 UUID")
    title: Optional[str] = Field(None, description="可选的会话标题")

class ChatSessionRead(BaseModel):
    uuid: str
    title: Optional[str]
    agent_instance_uuid: str = Field(..., alias="agent_instance.uuid")
    message_count: int
    updated_at: datetime
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class ChatSessionUpdate(BaseModel):
    title: Optional[str] = None
    is_archived: Optional[bool] = None

# --- Message Schemas ---

class ChatMessageRead(BaseModel):
    uuid: str
    role: MessageRole
    content: Optional[str]
    meta: Optional[Dict[str, Any]] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    trace_id: Optional[str] = None
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class ContextClearRequest(BaseModel):
    """清空上下文的请求参数"""
    mode: str = Field("production", description="模式: 'production' (软删) 或 'debug' (物理删)")