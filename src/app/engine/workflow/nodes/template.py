# app/engine/workflow/nodes/template.py
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict
from ..definitions import WorkflowNode, NodeData, BaseNodeConfig, ExecutionPolicy, NodeTemplate, NodeCategory
from ...schemas.parameter_schema import ParameterSchema 
from ...schemas.form_schema import FormProperty

# ============================================================================
# 1. Start Node Template
# ============================================================================
class StartNodeConfig(BaseNodeConfig):
    model_config = ConfigDict(extra="forbid")

START_TEMPLATE = NodeTemplate(
    category=NodeCategory.COMMON,
    icon="play",
    data=NodeData(
        registryId="Start",
        name="开始",
        description="工作流的起始节点。",
        inputs=[],
        outputs=[], # Start 的 outputs 通常由用户定义
        config=StartNodeConfig()
    ),
    forms=[]
)

# ============================================================================
# 2. End / Output Node Template
# ============================================================================
class OutputNodeConfig(BaseNodeConfig):
    """End 节点和 Output 节点的配置结构"""
    stream: bool = Field(default=False, description="是否作为流式生产者")
    returnType: Optional[Literal["Object", "Text"]] = Field(default="Object", description="返回类型")
    content: Optional[str] = Field(None, description="输出内容的模板 (当 returnType=Text 时有效)")
    model_config = ConfigDict(extra="forbid")

OUTPUT_TEMPLATE = NodeTemplate(
    category=NodeCategory.COMMON,
    icon="stop",
    data=NodeData(
        registryId="Output",
        name="输出",
        description="中间输出节点。",
        inputs=[], 
        outputs=[],
        config=OutputNodeConfig(returnType="Object")
    ),
    forms=[
        FormProperty(
            label="输出方式",
            type="form",
            form_type="radio_group",
            output_key="config.returnType",
            props={"options": [{"label": "结构化对象", "value": "Object"}, {"label": "纯文本", "value": "Text"}]},
            show_expr=True,
            required_expr=True
        )
    ]
)

END_TEMPLATE = NodeTemplate(
    category=NodeCategory.COMMON,
    icon="stop",
    data=NodeData(
        registryId="End",
        name="结束",
        description="工作流的结束节点。",
        inputs=[], 
        outputs=[],
        config=OutputNodeConfig(returnType="Object")
    ),
    forms=[
        FormProperty(
            label="输出方式",
            type="form",
            form_type="radio_group",
            output_key="config.returnType",
            props={"options": [{"label": "结构化对象", "value": "Object"}, {"label": "纯文本", "value": "Text"}]},
            show_expr=True,
            required_expr=True
        )
    ]
)

# ============================================================================
# 3. Branch Node Template
# ============================================================================
class BranchLogic(str):
    AND = "&"
    OR = "|"

class BranchCondition(BaseModel):
    operator: int = Field(..., description="操作符ID (1-10)")
    left: ParameterSchema 
    right: ParameterSchema

class BranchGroup(BaseModel):
    id: Optional[str] = None
    logic: str = Field(default="&") # 使用字符串以便序列化
    conditions: List[BranchCondition] = []

class BranchNodeConfig(BaseNodeConfig):
    branchs: List[BranchGroup] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid")

BRANCH_TEMPLATE = NodeTemplate(
    category=NodeCategory.COMMON,
    icon="stop",
    data=NodeData(
        registryId="Branch",
        name="条件分支",
        description="条件分支节点。",
        inputs=[], 
        outputs=[],
        config=BranchNodeConfig()
    ),
    forms=[]
)

# ============================================================================
# 4. Loop Node Template
# ============================================================================
class LoopNodeConfig(BaseNodeConfig):
    loopType: Literal["count", "list"] = Field(default="count")
    loopCount: Optional[ParameterSchema] = None 
    loopList: Optional[ParameterSchema] = None
    model_config = ConfigDict(extra="forbid")

LOOP_TEMPLATE = NodeTemplate(
    category=NodeCategory.COMMON,
    icon="play",
    data=NodeData(
        registryId="Loop",
        name="循环",
        description="循环节点。",
        inputs=[],
        outputs=[],
        config=LoopNodeConfig()
    ),
    forms=[
        FormProperty(
            label="循环模式",
            desc="按固定次数循环或遍历列表。",
            type="form",
            form_type="radio_group",
            output_key="config.loopType",
            props={
                "options": [
                    {"label": "次数", "value": "count"},
                    {"label": "列表", "value": "list"},
                ]
            },
            show_expr=True,
            required_expr=True,
        ),
        FormProperty(
            label="循环次数参数",
            desc="定义循环次数，支持字面量/表达式/变量引用。",
            type="form",
            form_type="parameter_schema",
            output_key="config.loopCount",
            props={
                "default_schema": {"name": "loopCount", "type": "integer"},
            },
            show_expr="config.loopType == 'count'",
            required_expr="config.loopType == 'count'",
            form_role="input",
        ),
        FormProperty(
            label="循环列表参数",
            desc="定义要遍历的数组，支持变量引用。",
            type="form",
            form_type="parameter_schema",
            output_key="config.loopList",
            props={
                "default_schema": {
                    "name": "loopList",
                    "type": "array",
                    "items": {"type": "string"},
                }
            },
            show_expr="config.loopType == 'list'",
            required_expr="config.loopType == 'list'",
            form_role="input",
        ),
    ]
)
