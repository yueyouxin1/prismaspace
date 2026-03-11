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
    form_type: Optional[str] = Field(default=None, description="旧版控件类型字段，兼容保留")

    # 传递给前端组件的 props
    props: Dict[str, Any] = Field(default_factory=dict)
    ui: Dict[str, Any] = Field(default_factory=dict, description="布局/样式提示，与前端 generator ui 对齐")
    meta: Dict[str, Any] = Field(default_factory=dict, description="业务扩展元数据")

    # 嵌套结构
    children: Optional[List['FormProperty']] = None

    # 绑定值的路径（相对于 NodeData 根对象，不再限定只落在 config）
    model_path: Optional[str] = Field(default=None, description="绑定值的路径，与前端 generator modelPath 对齐")
    output_key: Optional[str] = Field(default=None, description="旧版绑定键路径字段，兼容保留")

    # generator 契约字段
    state: Dict[str, Any] = Field(default_factory=dict, description="显隐/禁用等状态，与前端 generator state 对齐")
    required: Optional[bool] = Field(default=None, description="静态必填")
    required_when: Union[str, bool, None] = Field(default=None, description="动态必填条件，与前端 generator requiredWhen 对齐")
    role: Optional[str] = Field(default='default', description="表单角色，与前端 generator role 对齐")

    # 动态控制表达式
    show_expr: Union[str, bool, None] = Field(default=None, description="旧版显隐控制表达式")
    disabled_expr: Union[str, bool, None] = Field(default=None, description="旧版禁用控制表达式")
    required_expr: Union[str, bool, None] = Field(default=None, description="旧版必填控制表达式")

    # action 契约
    action_type: Optional[str] = Field(default=None, description="action 类型，与前端 generator actionType 对齐")
    renderer: Optional[str] = Field(default=None, description="action 渲染器标识")
    on: Optional[Dict[str, Any]] = Field(default=None, description="声明式 action 定义")

    # 兼容旧字段
    form_role: Optional[str] = Field(default=None, description="旧版表单角色字段，兼容保留")

    model_config = ConfigDict(extra='ignore')

    @model_validator(mode='after')
    def normalize_contract(self):
        if not self.control and self.form_type:
            self.control = self.form_type
        if not self.form_type and self.control:
            self.form_type = self.control

        if not self.model_path and self.output_key:
            self.model_path = self.output_key
        if not self.output_key and self.model_path:
            self.output_key = self.model_path

        if not self.id:
            stable_key = self.model_path or self.output_key or self.label or self.control or self.action_type or 'item'
            self.id = str(stable_key).replace('.', '_').replace('[', '_').replace(']', '').replace('/', '_')

        if self.role is None and self.form_role is not None:
            self.role = self.form_role
        if self.form_role is None and self.role is not None:
            self.form_role = self.role

        state = dict(self.state or {})
        if 'visible' not in state and self.show_expr is not None:
            state['visible'] = self.show_expr
        if 'disabled' not in state and self.disabled_expr is not None:
            state['disabled'] = self.disabled_expr
        self.state = state

        if self.required_when is None and self.required_expr is not None:
            self.required_when = self.required_expr
        if self.required is None and self.required_expr is True:
            self.required = True

        if self.type == 'action' and not self.action_type:
            self.action_type = 'button'

        return self

# 解决递归引用
FormProperty.model_rebuild()
