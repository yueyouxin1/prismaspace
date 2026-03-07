# src/app/schemas/interaction/chat_schemas.py

from pydantic import BaseModel, Field, ConfigDict, model_validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from app.models.interaction.chat import MessageRole
from app.models.interaction.chat import ChatMessage, ChatSession

# --- Session Schemas ---

class ChatSessionCreate(BaseModel):
    agent_instance_uuid: str = Field(..., description="要交互的 Agent 实例 UUID")
    title: Optional[str] = Field(None, description="可选的会话标题")

class ChatSessionRead(BaseModel):
    uuid: str
    title: Optional[str]
    agent_instance_uuid: str
    message_count: int
    updated_at: datetime
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode='before')
    @classmethod
    def pre_process_orm_obj(cls, data: Any) -> Any:
        if not isinstance(data, ChatSession):
            return data

        agent_instance = getattr(data, "agent_instance", None)
        agent_instance_uuid = getattr(agent_instance, "uuid", None)

        return {
            "uuid": data.uuid,
            "title": data.title,
            "agent_instance_uuid": agent_instance_uuid,
            "message_count": data.message_count,
            "updated_at": data.updated_at,
            "created_at": data.created_at,
        }

class ChatSessionUpdate(BaseModel):
    title: Optional[str] = None

# --- Message Schemas ---

class ChatMessageRead(BaseModel):
    uuid: str
    role: MessageRole
    content: Optional[str]
    text_content: Optional[str] = None
    content_parts: Optional[List[Dict[str, Any]]] = None
    reasoning_content: Optional[str] = None
    activity_type: Optional[str] = None
    encrypted_value: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    run_id: Optional[str] = None
    turn_id: Optional[str] = None
    trace_id: Optional[str] = None
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode='before')
    @classmethod
    def pre_process_message(cls, data: Any) -> Any:
        if not isinstance(data, ChatMessage):
            return data

        return {
            "uuid": data.uuid,
            "role": data.role,
            "content": data.content,
            "text_content": data.text_content,
            "content_parts": data.content_parts,
            "reasoning_content": data.reasoning_content,
            "activity_type": data.activity_type,
            "encrypted_value": data.encrypted_value,
            "meta": data.meta,
            "tool_calls": data.tool_calls,
            "tool_call_id": data.tool_call_id,
            "run_id": data.run_id,
            "turn_id": data.turn_id,
            "trace_id": data.trace_id,
            "created_at": data.created_at,
        }

class ContextClearRequest(BaseModel):
    """清空上下文的请求参数"""
    mode: str = Field("production", description="模式: 'production' (软删) 或 'debug' (物理删)")
