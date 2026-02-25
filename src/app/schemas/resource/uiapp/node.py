# src/app/schemas/resource/uiapp/node.py

from __future__ import annotations
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Dict, Any, Optional
from .common import ValueProperty, StyleProperty
from .action import UiAction

# 对应 contracts/Event.ts
# EventHandler: { "onClick": [Action, ...], "onLoad": [...] }
EventHandler = Dict[str, List[UiAction]]

# 对应 contracts/Node.ts
class UiNode(BaseModel):
    """
    DOM 节点定义。
    Strictly aligned with contracts/Node.ts
    """
    id: str

    semanticRole: str = Field(..., description="Component type, e.g., 'Button', 'Container'")
    
    label: Optional[str] = None
    
    # 核心属性
    style: Optional[StyleProperty] = None

    state: Optional[Dict[str, ValueProperty]] = None

    options: Optional[Dict[str, ValueProperty]] = None 
    
    # 事件与交互
    event: Optional[EventHandler] = None
    
    # 递归结构
    children: Optional[List[UiNode]] = None

    # [关键配置] 允许前端发送 Pydantic 未定义的额外字段
    # 这保证了如果前端增加了新的非核心字段（如 `animation`），后端不会报错
    model_config = ConfigDict(extra='allow')

# 解决递归
UiNode.model_rebuild()