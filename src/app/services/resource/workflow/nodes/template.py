# app/services/resource/workflow/nodes/template.py
from typing import Any, Dict, List, Optional, Literal
from pydantic import Field, ConfigDict
from app.engine.workflow.definitions import WorkflowNode, NodeData, BaseNodeConfig, ExecutionPolicy, NodeTemplate, NodeCategory
from app.engine.schemas.parameter_schema import ParameterSchema 
from app.engine.model.llm import LLMMessage
from app.schemas.resource.agent.agent_schemas import AgentSchema

class ResourceNodeConfig(BaseNodeConfig):
    resource_instance_uuid: str = Field(...)
    model_config = ConfigDict(extra="forbid")

# ============================================================================
# 1. LLM Node Template
# ============================================================================
# A. 定义配置模型 (仅用于构建默认值和类型提示，非运行时强制依赖)
class LLMNodeConfig(BaseNodeConfig, AgentSchema):
    history: List[LLMMessage] = Field(default_factory=list, description="上下文")
    model_config = ConfigDict(extra="forbid")

# B. 定义模版
LLM_TEMPLATE = NodeTemplate(
    category=NodeCategory.MODEL,
    icon="cpu",
    # 核心预设数据 (NodeData)
    data=NodeData(
        registryId="LLMNode",
        name="大语言模型",
        description="调用系统集成的 LLM 模型进行文本生成。",
        # 预设参数
        inputs=[],
        outputs=[ParameterSchema(name="text", type="string", label="生成结果")],
        # 预设配置 (使用 Config 类的默认值)
        config=LLMNodeConfig(llm_module_version_uuid="")
    )
)

# ============================================================================
# 2. Agent Node Template
# ============================================================================
# Agent Node采取无状态对话
class AgentNodeConfig(ResourceNodeConfig):
    input_query: str = Field(default="", description="用户输入")
    input_content_parts: Optional[List[Dict[str, Any]]] = Field(default=None, description="多模态输入")
    history: Optional[List[LLMMessage]] = Field(default=None, description="可选历史上下文")
    session_uuid: Optional[str] = Field(default=None, description="会话ID（可选）")
    enable_session: Optional[bool] = Field(default=None, description="是否启用持久会话")
    model_config = ConfigDict(extra="forbid")

AGENT_TEMPLATE = NodeTemplate(
    category=NodeCategory.AGENT,
    icon="cpu",
    data=NodeData(
        registryId="AgentNode",
        name="Agent智能体",
        description="调用已有的 Agent资源完成任务。",
        # 预设参数
        inputs=[],
        outputs=[ParameterSchema(name="response", type="string", label="生成结果")],
        # 预设配置 (使用 Config 类的默认值)
        config=AgentNodeConfig(resource_instance_uuid="", input_query="")
    )
)

# ============================================================================
# 2. Tool Node Template
# ============================================================================
class ToolNodeConfig(ResourceNodeConfig):
    model_config = ConfigDict(extra="forbid")

TOOL_TEMPLATE = NodeTemplate(
    category=NodeCategory.TOOL,
    icon="tool",
    data=NodeData(
        registryId="ToolNode",
        name="工具",
        description="调用工作空间内的工具资源。",
        inputs=[], # Tool 的输入是动态的
        outputs=[],
        config=ToolNodeConfig(resource_instance_uuid="")
    )
)


# ============================================================================
# 3. Workflow Node Template
# ============================================================================
class WorkflowNodeConfig(ResourceNodeConfig):
    model_config = ConfigDict(extra="forbid")


WORKFLOW_TEMPLATE = NodeTemplate(
    category=NodeCategory.LOGIC,
    icon="git-branch",
    data=NodeData(
        registryId="WorkflowNode",
        name="子工作流",
        description="调用已有的 Workflow 资源并等待其完成。",
        inputs=[],
        outputs=[],
        config=WorkflowNodeConfig(resource_instance_uuid=""),
    ),
)
