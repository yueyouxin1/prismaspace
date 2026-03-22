# app/engine/workflow/nodes/template.py
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict
from ..definitions import WorkflowNode, NodeData, BaseNodeConfig, ExecutionPolicy, NodeTemplate, NodeCategory
from ...schemas.parameter_schema import ParameterSchema 

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
    )
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
    )
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
    )
)

# ============================================================================
# 4. Set Variable Node Template
# ============================================================================
class SetVariableAssignment(BaseModel):
    left: ParameterSchema
    right: ParameterSchema


class SetVariableNodeConfig(BaseNodeConfig):
    assignments: List[SetVariableAssignment] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid")


SET_VARIABLE_TEMPLATE = NodeTemplate(
    category=NodeCategory.LOGIC,
    icon="refresh-cw",
    data=NodeData(
        registryId="SetVariable",
        name="设置变量",
        description="用于重置循环变量的值，使其后续循环使用重置后的值。",
        inputs=[],
        outputs=[],
        config=SetVariableNodeConfig(),
    ),
)

# ============================================================================
# 5. Loop Node Template
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
    )
)


# ============================================================================
# 6. Interrupt Node Template
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
)
