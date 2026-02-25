from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Dict, Any, List, Union

# --- 复用底层引擎定义 ---
from app.engine.agent import AgentInput, AgentResult
from app.engine.model.llm import LLMRunConfig, LLMUsage, LLMMessage
from app.engine.workflow import NodeResultData
from app.schemas.resource.knowledge.knowledge_schemas import KnowledgeBaseExecutionParams, GroupedSearchResult
from app.schemas.resource.tenantdb.tenantdb_schemas import TenantDbExecutionParams, TenantDbExecutionResponse

class BaseTraceAttributes(BaseModel):
    """
    Trace Attributes 的基类协议。
    采用泛型设计，允许子类精确定义 inputs/outputs/meta 的类型。
    """
    # 这里使用 Any 是为了基类的通用性，子类会覆盖为具体类型
    inputs: Any = Field(default_factory=dict, description="操作的输入参数快照")
    outputs: Optional[Any] = Field(None, description="操作的输出结果快照")
    meta: Optional[Any] = Field(default_factory=dict, description="额外元数据 (Latency, IP, etc.)")
    
    model_config = ConfigDict(arbitrary_types_allowed=True)

# ==============================================================================
# 1. Agent 场景 (最上层编排)
# ==============================================================================

class AgentMeta(BaseModel):
    # Agent 运行时的配置快照，便于复现
    config: LLMRunConfig = Field(..., description="temperature, top_p 等")

class AgentAttributes(BaseTraceAttributes):
    """Agent 执行的专用属性结构"""
    # 直接复用 AgentInput (包含 messages 历史)
    inputs: AgentInput
    # 直接复用 AgentResult (包含 final_answer 和 intermediate_steps)
    # 这是最关键的调试信息，包含了完整的思维链
    outputs: Optional[AgentResult] = None
    meta: Optional[AgentMeta] = None

# ==============================================================================
# 2. LLM 场景 (底层生成)
# ==============================================================================

class LLMMeta(BaseModel):
    provider: str
    model: str
    # 完整的运行参数，这对调试 "为什么模型胡说八道" 至关重要
    run_config: LLMRunConfig
    token_usage: Optional[LLMUsage] = None

class LLMAttributes(BaseTraceAttributes):
    """LLM 调用的专用属性结构"""
    # 输入通常是 Prompt 或 Messages 列表
    inputs: Dict[str, Any] # e.g. {"messages": [...]}
    # 输出通常是生成的文本或 ToolCall
    outputs: Optional[LLMMessage] = None
    meta: Optional[LLMMeta] = None

# ==============================================================================
# 3. Tool 场景 (原子能力)
# ==============================================================================

class ToolMeta(BaseModel):
    tool_name: str
    http_method: str = "GET"
    http_status: Optional[int] = None
    url: Optional[str] = None

class ToolAttributes(BaseTraceAttributes):
    """工具调用的专用属性结构"""
    # 工具输入通常是一个字典 (arguments)
    inputs: Dict[str, Any]
    # 工具输出也是字典或原始值
    outputs: Optional[Any] = None
    meta: Optional[ToolMeta] = None

# ==============================================================================
# 4. KnowledgeBase 场景 (知识检索)
# ==============================================================================

class KnowledgeBaseMeta(BaseModel):
    collection_name: str
    engine_alias: str

class KnowledgeBaseAttributes(BaseTraceAttributes):
    """向量检索的专用属性结构"""
    # 复用检索参数定义
    inputs: KnowledgeBaseExecutionParams
    # 输出是检索结果块的列表
    outputs: Optional[List[GroupedSearchResult]] = None
    meta: Optional[KnowledgeBaseMeta] = None

# ==============================================================================
# 5. TenantDB 场景 
# ==============================================================================
class TenantDBMeta(BaseModel):
    table_name: str
    action: str # query, insert, etc.
    sql_preview: Optional[str] = None
    row_count: Optional[int] = None

class TenantDBAttributes(BaseTraceAttributes):
    inputs: TenantDbExecutionParams
    outputs: Optional[TenantDbExecutionResponse] = None # 可能是 List, Dict, 或 int
    meta: Optional[TenantDBMeta] = None

# ==============================================================================
# 6. Workflow 场景
# ==============================================================================

class WorkflowAttributes(BaseTraceAttributes):
    inputs: Dict[str, Any] = Field(default_factory=dict)
    outputs: Optional[NodeResultData] = None

class WorkflowNodeMeta(BaseModel):
    node_id: str
    node_name: str
    node_method: str
    node_config: Dict[str, Any]

class WorkflowNodeAttributes(BaseTraceAttributes):
    inputs: Dict[str, Any] = Field(default_factory=dict)
    outputs: Optional[NodeResultData] = None
    meta: WorkflowNodeMeta
