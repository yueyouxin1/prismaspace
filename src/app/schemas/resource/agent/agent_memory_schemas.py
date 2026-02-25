from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Optional, Any
from datetime import datetime
from app.models.resource.agent.agent_memory import MemoryType, MemoryScope

class AgentMemoryVarBase(BaseModel):
    key: str = Field(..., pattern=r'^[a-zA-Z_][a-zA-Z0-9_]*$', description="变量键名")
    label: str = Field(..., min_length=1, max_length=64)
    type: MemoryType = Field(default=MemoryType.STRING)
    scope_type: MemoryScope = Field(default=MemoryScope.USER)
    description: Optional[str] = None
    default_value: Optional[Any] = None # 接收任意类型，Service层负责序列化
    is_active: bool = True

class AgentMemoryVarCreate(AgentMemoryVarBase):
    pass

class AgentMemoryVarUpdate(BaseModel):
    label: Optional[str] = None
    description: Optional[str] = None
    default_value: Optional[Any] = None
    is_active: Optional[bool] = None
    # key, type, scope 通常不建议轻易修改，容易造成数据不一致

class AgentMemoryVarRead(AgentMemoryVarBase):
    id: int
    agent_id: int
    # 注意：Read 视图不包含 runtime value，那是 Debug 接口的事
    model_config = ConfigDict(from_attributes=True)

class AgentContextSummaryCreate(BaseModel):
    content: str
    scope: str
    session_uuid: Optional[str] = None

class AgentContextSummaryRead(BaseModel):
    uuid: str
    content: str
    scope: str
    trace_id: str
    session_uuid: Optional[str]
    created_at: datetime
    # 可以在这里扩展 module_name 等信息
    
    model_config = ConfigDict(from_attributes=True)