# app/engine/workflow/nodes/template.py
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict
from ..definitions import WorkflowNode, NodeData, BaseNodeConfig, ExecutionPolicy, NodeTemplate, NodeCategory
from ...schemas.parameter_schema import ParameterSchema 
from ...schemas.form_schema import FormProperty


def form_item(
    *,
    id: str,
    label: str,
    control: str,
    model_path: str,
    desc: Optional[str] = None,
    props: Optional[dict] = None,
    role: str = "default",
    required: Optional[bool] = None,
    required_when=None,
    visible=True,
    disabled=False,
) -> FormProperty:
    return FormProperty(
        id=id,
        label=label,
        desc=desc,
        type="form",
        control=control,
        form_type=control,
        model_path=model_path,
        output_key=model_path,
        props=props or {},
        role=role,
        form_role=role,
        required=required,
        required_when=required_when,
        state={"visible": visible, "disabled": disabled},
        show_expr=visible,
        disabled_expr=disabled,
        required_expr=required_when if required_when is not None else required,
    )


def schema_form(
    *,
    id: str,
    label: str,
    model_path: str,
    desc: Optional[str] = None,
) -> FormProperty:
    return form_item(
        id=id,
        label=label,
        control="parameter_schema",
        model_path=model_path,
        desc=desc,
        role="schema",
        props={
            "editor_kind": "regular",
            "layout": "compact",
            "collection": True,
        },
    )

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
    forms=[
        schema_form(
            id="start_outputs",
            label="输入",
            model_path="outputs",
            desc="定义工作流启动时可接收的输入变量。",
        ),
    ]
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
        schema_form(
            id="output_inputs",
            label="输入",
            model_path="inputs",
            desc="定义输出节点可消费的输入参数。",
        ),
        form_item(
            id="output_return_type",
            label="输出方式",
            control="radio_group",
            model_path="config.returnType",
            props={"options": [{"label": "结构化对象", "value": "Object"}, {"label": "纯文本", "value": "Text"}]},
            required=True,
        ),
        form_item(
            id="output_content",
            label="输出模板",
            control="textarea",
            model_path="config.content",
            desc="当输出方式为纯文本时，定义最终输出模板。",
            visible="config.returnType == 'Text'",
            required_when="config.returnType == 'Text'",
        ),
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
        schema_form(
            id="end_inputs",
            label="输入",
            model_path="inputs",
            desc="定义工作流结束节点的返回值结构。",
        ),
        form_item(
            id="end_return_type",
            label="输出方式",
            control="radio_group",
            model_path="config.returnType",
            props={"options": [{"label": "结构化对象", "value": "Object"}, {"label": "纯文本", "value": "Text"}]},
            required=True,
        ),
        form_item(
            id="end_content",
            label="输出模板",
            control="textarea",
            model_path="config.content",
            desc="当输出方式为纯文本时，定义最终输出模板。",
            visible="config.returnType == 'Text'",
            required_when="config.returnType == 'Text'",
        ),
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
    forms=[
        schema_form(
            id="branch_inputs",
            label="输入",
            model_path="inputs",
            desc="定义条件分支节点的输入变量。",
        ),
        schema_form(
            id="branch_outputs",
            label="输出",
            model_path="outputs",
            desc="定义条件分支节点的输出变量。",
        ),
    ]
)

# ============================================================================
# 4. Loop Node Template
# ============================================================================
class LoopNodeConfig(BaseNodeConfig):
    loopType: Literal["count", "list"] = Field(default="count")
    executionMode: Literal["serial", "parallel"] = Field(default="serial")
    maxConcurrency: int = Field(default=1, ge=1)
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
        schema_form(
            id="loop_inputs",
            label="输入",
            model_path="inputs",
            desc="定义循环节点的输入变量。",
        ),
        schema_form(
            id="loop_outputs",
            label="输出",
            model_path="outputs",
            desc="定义循环节点的输出变量。",
        ),
        form_item(
            id="loop_type",
            label="循环模式",
            desc="按固定次数循环或遍历列表。",
            control="radio_group",
            model_path="config.loopType",
            props={
                "options": [
                    {"label": "次数", "value": "count"},
                    {"label": "列表", "value": "list"},
                ]
            },
            required=True,
        ),
        form_item(
            id="loop_count",
            label="循环次数参数",
            desc="定义循环次数，支持字面量/表达式/变量引用。",
            control="parameter_schema",
            model_path="config.loopCount",
            props={
                "default_schema": {"name": "loopCount", "type": "integer"},
                "collection": False,
            },
            visible="config.loopType == 'count'",
            required_when="config.loopType == 'count'",
            role="input",
        ),
        form_item(
            id="loop_list",
            label="循环列表参数",
            desc="定义要遍历的数组，支持变量引用。",
            control="parameter_schema",
            model_path="config.loopList",
            props={
                "default_schema": {
                    "name": "loopList",
                    "type": "array",
                    "items": {"type": "string"},
                },
                "collection": False,
            },
            visible="config.loopType == 'list'",
            required_when="config.loopType == 'list'",
            role="input",
        ),
        form_item(
            id="loop_execution_mode",
            label="执行模式",
            desc="串行执行或并发批处理。",
            control="radio_group",
            model_path="config.executionMode",
            props={
                "options": [
                    {"label": "串行", "value": "serial"},
                    {"label": "并行", "value": "parallel"},
                ]
            },
            required=True,
        ),
        form_item(
            id="loop_max_concurrency",
            label="最大并发数",
            desc="仅在并行模式下生效，控制单次 fan-out 并发度。",
            control="input_number",
            model_path="config.maxConcurrency",
            props={"min": 1, "step": 1},
            visible="config.executionMode == 'parallel'",
            required_when="config.executionMode == 'parallel'",
        ),
    ]
)


# ============================================================================
# 5. Interrupt Node Template
# ============================================================================
class InterruptNodeConfig(BaseNodeConfig):
    reason: str = Field(default="user_input_required")
    message: str = Field(default="Workflow requires user input before continuing.")
    resume_output_key: str = Field(default="resume")
    model_config = ConfigDict(extra="forbid")


INTERRUPT_TEMPLATE = NodeTemplate(
    category=NodeCategory.LOGIC,
    icon="pause-circle",
    data=NodeData(
        registryId="Interrupt",
        name="人工确认",
        description="中断工作流并等待外部输入后恢复执行。",
        inputs=[],
        outputs=[ParameterSchema(name="resume", type="object", required=False)],
        config=InterruptNodeConfig(),
    ),
    forms=[
        schema_form(
            id="interrupt_outputs",
            label="输出",
            model_path="outputs",
            desc="定义恢复执行后输出的变量结构。",
        ),
        form_item(
            id="interrupt_reason",
            label="中断原因",
            control="input",
            model_path="config.reason",
            required=True,
        ),
        form_item(
            id="interrupt_message",
            label="提示信息",
            control="textarea",
            model_path="config.message",
            required=True,
        ),
        form_item(
            id="interrupt_resume_output_key",
            label="恢复输出键",
            control="input",
            model_path="config.resume_output_key",
            required=True,
        ),
    ],
)
