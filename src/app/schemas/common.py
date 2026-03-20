# app/schemas/common.py

import json
from pydantic import BaseModel, Field
from typing import Generic, TypeVar, Dict, Any, Optional

T = TypeVar('T')  # 定义泛型类型

class JsonResponse(BaseModel, Generic[T]):  # 继承 Generic[T]
    data: T  # 使用泛型类型 T
    msg: str = "success"
    status: int = 200

class JsonFaildResponse(BaseModel, Generic[T]):
    data: Optional[T] = None
    msg: str = "error"
    status: int = 400

class MsgResponse(BaseModel):
    msg: str = "success"

class SSEvent(BaseModel):
    """运行时产生的原子事件"""
    id: Optional[str] = None
    event: str
    data: Dict[str, Any]
    
    def to_sse(self) -> str:
        parts = []
        if self.id:
            parts.append(f"id: {self.id}")
        parts.append(f"event: {self.event}")
        parts.append(f"data: {json.dumps(self.data, ensure_ascii=False)}")
        return "\n".join(parts) + "\n\n"

class ExecutionRequest(BaseModel):
    """
    The foundational, generic request body for any resource execution.
    It contains the 'inputs' dictionary that will be validated by more specific schemas.
    """
    # 用于传递特定于执行的元参数
    meta: Optional[Dict[str, Any]] = Field(None, description="Execution-specific options, not part of the resource's business inputs.")
    inputs: Dict[str, Any] = Field(default_factory=dict, description="The runtime parameters for the resource instance.")

class ExecutionResponse(BaseModel):
    success: bool = True
    data: Dict[str, Any] = Field(...)
    error_message: Optional[str] = None
