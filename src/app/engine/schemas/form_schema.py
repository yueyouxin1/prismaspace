from typing import List, Dict, Any, Optional, Union, Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator

# ==============================================================================
# 表单协议 (Form Protocol)
# ==============================================================================
class FormProperty(BaseModel):
    """
    前端动态表单的渲染协议。
    用于描述如何渲染 NodeData.config 的编辑界面。
    """
    id: Optional[str] = Field(default=None, description="稳定唯一标识，优先与前端 generator 的 id 对齐")
    label: Optional[str] = Field(default=None, description="表单项标签")
    desc: Optional[str] = Field(None, description="表单项描述/提示")
    type: Literal['form', 'action'] = Field('form', description="类型：常规表单项 或 动作按钮")
    control: Optional[str] = Field(default=None, description="前端动态表单控件类型，与 generator registry key 对齐")

    # 传递给前端组件的 props
    props: Dict[str, Any] = Field(default_factory=dict)
    ui: Dict[str, Any] = Field(default_factory=dict, description="布局/样式提示，与前端 generator ui 对齐")
    meta: Dict[str, Any] = Field(default_factory=dict, description="业务扩展元数据")

    # 嵌套结构
    children: Optional[List['FormProperty']] = None

    # 绑定值的路径（相对于 NodeData 根对象，不再限定只落在 config）
    model_path: Optional[str] = Field(default=None, description="绑定值的路径，与前端 generator modelPath 对齐")

    # generator 契约字段
    state: Dict[str, Any] = Field(default_factory=dict, description="显隐/禁用等状态，与前端 generator state 对齐")
    required: Union[str, bool, None] = Field(default=False, description="必填条件，支持布尔或表达式字符串")
    role: Optional[str] = Field(default='default', description="表单角色，与前端 generator role 对齐")

    # action 契约
    action_type: Optional[str] = Field(default=None, description="action 类型，与前端 generator actionType 对齐")
    renderer: Optional[str] = Field(default=None, description="action 渲染器标识")
    on: Optional[Dict[str, Any]] = Field(default=None, description="声明式 action 定义")

    model_config = ConfigDict(extra='ignore')

    @model_validator(mode='after')
    def normalize_contract(self):
        if not self.id:
            stable_key = self.model_path or self.label or self.control or self.action_type or 'item'
            self.id = str(stable_key).replace('.', '_').replace('[', '_').replace(']', '').replace('/', '_')

        state = dict(self.state or {})
        if 'visible' not in state:
            state['visible'] = True
        if 'disabled' not in state:
            state['disabled'] = False
        self.state = state

        if self.required is None:
            self.required = False

        if self.type == 'action' and not self.action_type:
            self.action_type = 'button'

        return self

# 解决递归引用
FormProperty.model_rebuild()
