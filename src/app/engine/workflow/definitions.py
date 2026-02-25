from __future__ import annotations
from enum import Enum
from typing import List, Dict, Any, Optional, Union, Literal, NamedTuple
from pydantic import BaseModel, Field, ConfigDict

from ..schemas.parameter_schema import ParameterSchema, ParameterValue
from ..schemas.form_schema import FormProperty

# ============================================================================
# 1. 权威状态定义 (Authoritative Status Definitions)
# ============================================================================

NodeStatus = Literal["PENDING", "RUNNING", "COMPLETED", "SKIPPED", "STREAMTASK", "STREAMSTART", "STREAMING", "STREAMEND", "FAILED"]

class StreamEvent(BaseModel):
    node_id: str
    status: NodeStatus = "STREAMSTART"
    content: Optional[str] = None

class ErrorBody(BaseModel):
    """标准错误信息结构"""
    message: str = Field(..., description="错误消息")
    type: str = Field(..., description="错误类型")
    data: Optional[Any] = Field(None, description="容错降级数据")

class RuntimeStatus(BaseModel):
    """节点运行时状态，用于容错和调试"""
    isSuccess: bool = Field(..., description="是否执行成功")
    errorBody: Optional[ErrorBody] = Field(None, description="错误详情")

class NodeResultData(BaseModel):
    """
    [权威定义] 节点执行的业务数据结构。
    这是引擎上下文中变量的实际形态。
    """
    output: Dict[str, Any] = Field(default_factory=dict, description="节点的结构化输出，变量树的根")
    content: Optional[str] = Field(None, description="节点的文本内容 (用于 Text 类型输出)")
    error_msg: Optional[str] = None

class NodeExecutionResult(BaseModel):
    """
    节点执行结果容器。
    data: 非流式时为 NodeResultData；流式时为 StreamBroadcaster。
    """
    input: Dict[str, Any] = Field(default_factory=dict, description="最终运行时输入")
    data: Union[NodeResultData, Any] 
    activated_port: str = "0"
    
# ============================================================================
# 2. 策略与控制流定义 (Policy & Control Flow)
#    这些是引擎核心调度逻辑(Orchestrator)直接依赖的数据结构
# ============================================================================

class ExecutionPolicy(BaseModel):
    """
    节点执行策略，严格对齐原型 execute_node 中的逻辑。
    """
    switch: bool = Field(default=False, description="是否开启策略")
    timeoutMs: int = Field(default=180000, description="超时时间(毫秒)")
    retryTimes: int = Field(default=0, description="重试次数")
    # 1=中断, 2=返回固定内容, 3=走异常分支(error端口)
    processType: int = Field(default=1, description="失败后的处理策略") 
    dataOnErr: Optional[str] = Field(None, description="当 processType=2 时返回的默认值")

class BranchLogic(str, Enum):
    """分支逻辑关系"""
    AND = "&"
    OR = "|"

class BranchCondition(BaseModel):
    """
    分支单条条件。
    原型逻辑：left/right 均被视为具备 value 结构的参数对象。
    """
    operator: int = Field(..., description="操作符ID (1-10)")
    # 复用 ParameterSchema，因为原型中通过 getBlockRefValue 解析 left['value']['content']
    # 这意味着 left/right 本质上就是参数定义
    left: ParameterSchema 
    right: ParameterSchema

class BranchGroup(BaseModel):
    """分支组"""
    id: Optional[str] = None
    logic: BranchLogic = Field(default=BranchLogic.AND)
    conditions: List[BranchCondition] = []

# ============================================================================
# 3. 节点配置容器 (Node Configuration)
#    采用“核心严格 + 扩展开放”的策略
# ============================================================================

class BaseNodeConfig(BaseModel):
    """
    节点 data.config 的通用基类，注册节点时继承它。
    """
    # --- 核心调度通用 ---
    executionPolicy: Optional[ExecutionPolicy] = None
    stream: bool = Field(default=False, description="是否作为流式生产者")
    returnType: Optional[str] = Field(None, description="返回类型，如 'Object' 或 'Text'")
    content: Optional[str] = Field(None, description="End/Output 节点的模板内容")

    # --- 默认宽松模式 ---
    model_config = ConfigDict(extra="allow")

# ============================================================================
# 4. 节点与边 (Nodes & Edges)
# ============================================================================

class WorkflowEdge(BaseModel):
    """工作流连线定义"""
    id: Optional[str] = None
    sourceNodeID: str
    targetNodeID: str
    sourcePortID: str
    targetPortID: str

class NodeData(BaseModel):
    """
    这里是引擎与业务数据的交汇点。
    """
    registryId: str = Field(..., min_length=1, max_length=50, description="开发者定义的全局唯一标识，用于锚定引擎中注册的节点函数。如 'Start', 'LLM', 'Agent'")
    name: str = Field("未命名", min_length=1, max_length=100, description="节点名称")
    description: str = Field("", description="节点描述")
    # 核心配置
    config: BaseNodeConfig = Field(default_factory=BaseNodeConfig)
    
    # 复用引擎统一的 ParameterSchema
    inputs: List[ParameterSchema] = Field(default_factory=list)
    outputs: List[ParameterSchema] = Field(default_factory=list)
    
    # --- Loop 节点特有：嵌套子图 ---
    # 使用 ForwardRef 解决 WorkflowNode 的递归引用
    blocks: Optional[List['WorkflowNode']] = Field(None, description="Loop节点的子节点列表")
    edges: Optional[List[WorkflowEdge]] = Field(None, description="Loop节点的子边列表")
    model_config = ConfigDict(extra="forbid")

class WorkflowNode(BaseModel):
    """工作流节点定义"""
    id: str = Field(None, description="自动生成。只保证当前工作流内唯一，用于审计等")
    data: NodeData = Field(..., description="适配前端画布框架，核心运行时数据")
    position: Optional[Dict[str, float]] = None # 坐标信息，引擎透传

# ============================================================================
# 5. 工作流整体结构 (Workflow Graph)
# ============================================================================

class WorkflowGraphDef(BaseModel):
    """
    对应 workflow.data JSON 结构。
    这是引擎执行的静态蓝图。
    """
    nodes: List[WorkflowNode] = Field(default_factory=list)
    edges: List[WorkflowEdge] = Field(default_factory=list)
    viewport: Optional[Dict[str, Any]] = None

# 手动更新前向引用，确保 WorkflowNode 中的 blocks 字段能正确解析 WorkflowNode 类型
NodeData.model_rebuild()
WorkflowNode.model_rebuild()

# ==============================================================================
# 6. 权威节点分类
# ==============================================================================
class NodeCategory(str, Enum):
    COMMON = "common"       # 通用 (Start, End)
    LOGIC = "logic"         # 逻辑 (Loop, Branch)
    MODEL = "model"         # 模型 (LLM)
    TOOL = "tool"           # 工具 (Api, WebBrowser)
    AGENT = "agent"         # 智能体 (Sub-Agent)
    DATA = "data"           # 数据操作 (Database, Knowledge)
    CUSTOM = "custom"       # 自定义扩展

# ==============================================================================
# 7. 节点模板 (Node Template) - 核心载体
# ==============================================================================
class NodeTemplate(BaseModel):
    """
    [权威定义] 节点模版。
    包含了节点的静态 UI 定义，应用层可持久化存储。
    """
    # --- UI 展示元数据 ---
    category: NodeCategory = Field(..., description="节点分类")
    icon: str = Field(..., description="图标标识")
    display_order: int = Field(0, description="排序权重")
    
    # --- 核心预设数据 (The Payload) ---
    data: NodeData = Field(..., description="预设节点数据")

    # --- 编辑器元数据 ---
    # 定义了如何渲染 node_data.config 的表单
    forms: List[FormProperty] = Field(default_factory=list, description="节点的配置表单定义")
    
    is_active: bool = Field(True)
    
    @property
    def registry_id(self) -> str:
        return self.data.registryId

    model_config = ConfigDict(arbitrary_types_allowed=True)