from typing import List, Dict, Any, Optional, Union, Literal
from pydantic import BaseModel, Field, ConfigDict

# ==============================================================================
# 表单协议 (Form Protocol)
# ==============================================================================
class FormProperty(BaseModel):
    """
    前端动态表单的渲染协议。
    用于描述如何渲染 NodeData.config 的编辑界面。
    """
    label: str = Field(..., description="表单项标签")
    desc: Optional[str] = Field(None, description="表单项描述/提示")
    type: Literal['form', 'action'] = Field('form', description="类型：常规表单项 或 动作按钮")
    form_type: str = Field(..., description="具体的控件类型 (e.g., 'input', 'select', 'code_editor')")
    
    # 传递给前端组件的 props
    props: Dict[str, Any] = Field(default_factory=dict)
    
    # 嵌套结构
    children: Optional[List['FormProperty']] = None
    
    # 绑定值的路径 (相对于 NodeData.config)
    output_key: str = Field(..., description="绑定值的键路径")
    
    # 动态控制表达式
    show_expr: Union[str, bool] = Field(True, description="显隐控制表达式")
    disabled_expr: Union[str, bool] = Field(False, description="禁用控制表达式")
    required_expr: Union[str, bool] = Field(False, description="必填控制表达式")
    
    form_role: str = Field('default', description="表单角色")

    model_config = ConfigDict(extra='ignore')

# 解决递归引用
FormProperty.model_rebuild()