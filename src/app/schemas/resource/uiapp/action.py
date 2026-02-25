# src/app/schemas/resource/uiapp/action.py

from __future__ import annotations
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Literal, Union, Dict, Any
from .common import ValueProperty

# 基础配置：自动转换 camelCase
def to_camel(string: str) -> str:
    parts = string.split('_')
    return parts[0] + ''.join(word.capitalize() for word in parts[1:])

class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True, # 允许通过 snake_case 构造
        extra='ignore'
    )

# 对应 contracts/Action.ts

class ActionBase(CamelModel):
    label: Optional[str] = None
    on_success: Optional[List[UiAction]] = None # JSON: onSuccess
    on_error: Optional[List[UiAction]] = None   # JSON: onError

# 兜底 Action
# 只要有 type 字段，就被视为合法 Action，但不进行深入解析
class GenericAction(ActionBase):
    type: str # 匹配任何字符串
    params: Optional[Dict[str, Any]] = None # 不校验参数结构
    
# 1. 控制元素
class ControlElementActionParams(CamelModel):
    target_id: ValueProperty[str] # JSON: targetId
    property: ValueProperty[str]
    value: Optional[ValueProperty[Any]] = None

class ControlElementAction(ActionBase):
    type: Literal['controlElement']
    params: ControlElementActionParams

# 2. 更新状态
class UpdateStateActionParams(CamelModel):
    property: ValueProperty[str]
    value: Optional[ValueProperty[Any]] = None

class UpdateStateAction(ActionBase):
    type: Literal['updateState']
    params: UpdateStateActionParams

# 3. 页面跳转
class NavigateToActionParams(CamelModel):
    type: ValueProperty[Literal['inside', 'outside']]
    page_id: ValueProperty[str] # JSON: pageId
    url: ValueProperty[str]
    inputs: Optional[List[Any]] = None 

class NavigateToAction(ActionBase):
    type: Literal['navigateTo']
    params: NavigateToActionParams

# 4. [核心] 执行工作流
class ExecuteWorkflowActionParams(CamelModel):
    workflow_id: ValueProperty[str] # JSON: workflowId
    inputs: Optional[List[Any]] = None

class ExecuteWorkflowAction(ActionBase):
    type: Literal['executeWorkflow']
    params: ExecuteWorkflowActionParams

# 5. 条件判断
class ConditionActionParams(CamelModel):
    if_expr: ValueProperty[str] # JSON: ifExpr
    then_actions: List[UiAction] # JSON: thenActions
    else_actions: Optional[List[UiAction]] = None # JSON: elseActions

class ConditionAction(ActionBase):
    type: Literal['condition']
    params: ConditionActionParams

# 6. 执行页面动作
class RunPageActionParams(CamelModel):
    name: ValueProperty[str]
    with_args: Optional[ValueProperty[Dict[str, Any]]] = None # JSON: withArgs

class RunPageAction(ActionBase):
    type: Literal['runPageAction']
    params: RunPageActionParams

# 联合类型
# Pydantic 会按顺序尝试匹配。
# 必须把 GenericAction 放在最后！
UiAction = Union[
    ControlElementAction, 
    UpdateStateAction, 
    NavigateToAction, 
    ExecuteWorkflowAction, 
    ConditionAction, 
    RunPageAction,
    GenericAction
]

# 解决递归引用
ActionBase.model_rebuild()
ConditionAction.model_rebuild()