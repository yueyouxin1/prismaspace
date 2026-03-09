# src/app/schemas/resource/agent/agent_schemas.py

from enum import Enum
from pydantic import BaseModel, Field, ConfigDict, model_validator, conint, confloat
from typing import Literal, Union, Dict, List, Any, Optional
from datetime import datetime
from app.schemas.resource.resource_schemas import InstanceUpdate, InstanceRead
from app.schemas.resource.knowledge.knowledge_schemas import RAGConfig
from app.schemas.resource.runtime_checkpoint import RuntimeCheckpointEnvelopeRead
from app.engine.model.llm import LLMMessage
from app.schemas.common import ExecutionRequest, ExecutionResponse
from app.schemas.protocol import RunAgentInputExt, RunEventsResponse
from app.models.resource.agent import AgentRunCheckpoint, AgentRunEvent, AgentToolExecution

# ==============================================================================
# 1. 配置子模型 (Sub-Configuration Models)
# ==============================================================================

class GenerationDiversity(str, Enum):
    PRECISE = "precise"   # 精确
    BALANCED = "balanced" # 平衡
    CREATIVE = "creative" # 创意
    CUSTOM = "custom"     # 自定义

class ModelParams(BaseModel):
    """
    底层模型参数配置。
    通常由 GenerationDiversity 自动填充，仅在 CUSTOM 模式下可手动编辑。
    """
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    top_p: float = Field(1.0, ge=0.0, le=1.0)
    presence_penalty: float = Field(0.0, ge=-2.0, le=2.0)
    frequency_penalty: float = Field(0.0, ge=-2.0, le=2.0)

class InputOutputConfig(BaseModel):
    """输入及输出设置"""
    history_turns: int = Field(default=10, ge=0, description="携带上下文轮数")
    max_response_tokens: int = Field(default=2048, ge=1, description="最大回复长度")
    enable_deep_thinking: bool = Field(default=False, description="深度思考开关 (如支持)")
    max_thinking_tokens: Optional[int] = Field(None, ge=1, description="最大思考长度 (仅当开启深度思考时有效)")
    response_format: Optional[Dict[str, Any]] = Field(
        default_factory=lambda: {"type": "text"},
        description="""返回内容的格式。支持三种模式：
        1. 文本模式: {"type": "text"}
        2. JSON Object模式: {"type": "json_object"} (需在提示词中包含JSON关键词)
        3. JSON Schema模式: {"type": "json_schema", "json_schema": {...}, "strict": true/false}
        """
    )

class NoRecallReplyConfig(BaseModel):
    """无召回回复配置"""
    enabled: bool = Field(default=False, description="是否启用无召回回复功能")
    reply_content: Optional[str] = Field(
        default="抱歉，我没有找到相关的信息。",
        description="无召回时的回复内容（仅在启用时生效）"
    )

class DeepMemoryConfig(BaseModel):
    """系统级深度记忆配置"""
    # 总开关
    enabled: bool = Field(default=False, description="是否启用深度记忆功能")
    
    # Layer 1: 长期上下文 (Vector Context)
    enable_vector_recall: bool = True 
    max_recall_turns: int = Field(default=2, ge=0, le=10, description="每次对话最大召回的历史轮次数量")
    min_match_score: float = Field(default=0.6, ge=0.0, le=1.0, description="向量召回的最小匹配度")
    
    # Layer 2: 上下文摘要 (Context Summary)
    enable_summarization: bool = Field(default=False, description="是否启用自动摘要生成")
    max_summary_turns: int = Field(default=5, ge=0, le=10, description="每次对话最大召回的摘要轮次数量")
    summary_scope: Literal["user", "session"] = Field(default="session", description="摘要的默认作用范围")
    # [关键新增] 指定用于生成摘要的模型。
    # 如果为 None，系统将使用 get_default_llm_module 获取默认模型 (e.g. gpt-3.5-turbo / qwen-turbo)
    summary_model_uuid: Optional[str] = Field(None, description="用于生成摘要的LLM模型版本UUID")

class AgentRAGConfig(RAGConfig):
    # 总开关
    enabled: bool = Field(default=False, description="是否启用RAG功能")
    # 回复行为
    no_recall_reply: NoRecallReplyConfig = Field(default_factory=NoRecallReplyConfig)
    show_source: bool = Field(default=True, description="是否在回复中显示引用来源")
    
    # 调度策略
    # auto: 由 Agent 自动决定是否调用 RAG (作为工具)
    # always: 强制每轮对话都进行检索
    call_method: Literal["auto", "always"] = Field(default="auto", description="RAG 调用方式")
    
# ==============================================================================
# 2. Agent 主配置模型 (Main Configuration Model)
# ==============================================================================

class AgentConfig(BaseModel):
    """Agent 的核心业务配置"""
    
    # --- 生成多样性 (Diversity) ---
    diversity_mode: GenerationDiversity = Field(
        default=GenerationDiversity.BALANCED, 
        description="生成多样性模式"
    )
    
    # 实际生效的模型参数 (Custom 模式下由用户指定，其他模式下自动覆盖)
    model_params: ModelParams = Field(default_factory=ModelParams)

    # --- 输入输出控制 ---
    io_config: InputOutputConfig = Field(default_factory=InputOutputConfig)

    # --- 深度记忆配置 ---
    deep_memory: DeepMemoryConfig = Field(default_factory=DeepMemoryConfig)

    # --- RAG 配置 ---
    rag_config: AgentRAGConfig = Field(default_factory=AgentRAGConfig)

    # --- UI 配置（前端编排工作台元信息，非执行核心逻辑） ---
    ui_config: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def apply_diversity_presets(self):
        """根据多样性模式自动调整模型参数"""
        mode = self.diversity_mode
        if mode == GenerationDiversity.PRECISE:
            self.model_params.temperature = 0.1
            self.model_params.top_p = 0.1
        elif mode == GenerationDiversity.BALANCED:
            self.model_params.temperature = 0.5
            self.model_params.top_p = 0.9
        elif mode == GenerationDiversity.CREATIVE:
            self.model_params.temperature = 0.9
            self.model_params.top_p = 1.0
        # Custom 模式下保持原值
        return self

# ==============================================================================
# 3. CRUD Schemas
# ==============================================================================

class AgentSchema(BaseModel):
    system_prompt: str = Field(default="You are a helpful AI assistant.")
    
    # [重构] 使用嵌套的 AgentConfig 替代扁平字段
    agent_config: AgentConfig = Field(default_factory=AgentConfig)
    
    llm_module_version_uuid: Optional[str] = Field(None, description="UUID of the ServiceModuleVersion (LLM) to use")

class AgentUpdate(AgentSchema, InstanceUpdate):
    pass

class AgentRead(InstanceRead, AgentSchema):
    model_config = ConfigDict(from_attributes=True)


# ==============================================================================
# 4. Execution Schemas
# ==============================================================================

class AgentExecutionRequest(ExecutionRequest):
    """
    统一资源执行端点下的 Agent 阻塞执行请求。
    使用 AG-UI 协议输入作为 inputs 载荷，不改变既有字段契约。
    """

    inputs: RunAgentInputExt = Field(..., description="AG-UI run input payload.")


class AgentExecutionResponse(ExecutionResponse):
    """
    统一资源执行端点下的 Agent 阻塞执行响应。
    data 字段承载 AG-UI RunEventsResponse。
    """

    data: RunEventsResponse


class AgentRunEventRead(BaseModel):
    sequence_no: int
    event_type: str
    payload: Dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def pre_process_event(cls, data: Any) -> Any:
        if isinstance(data, AgentRunEvent):
            return {
                "sequence_no": data.sequence_no,
                "event_type": data.event_type,
                "payload": data.payload or {},
                "created_at": data.created_at,
            }
        return data


class AgentToolExecutionRead(BaseModel):
    tool_call_id: str
    tool_name: str
    status: str
    step_index: Optional[int] = None
    thought: Optional[str] = None
    arguments: Optional[Dict[str, Any]] = None
    result: Optional[Any] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def pre_process_tool_execution(cls, data: Any) -> Any:
        if isinstance(data, AgentToolExecution):
            return {
                "tool_call_id": data.tool_call_id,
                "tool_name": data.tool_name,
                "status": data.status,
                "step_index": data.step_index,
                "thought": data.thought,
                "arguments": data.arguments,
                "result": data.result,
                "error_message": data.error_message,
                "created_at": data.created_at,
                "updated_at": data.updated_at,
            }
        return data


class AgentRunSummaryRead(BaseModel):
    run_id: str
    thread_id: str
    parent_run_id: Optional[str] = None
    status: str
    trace_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class AgentRunCheckpointRead(BaseModel):
    thread_id: str
    turn_id: str
    checkpoint_kind: str
    runtime_snapshot: Dict[str, Any] = Field(default_factory=dict)
    pending_client_tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    canonical: Optional[RuntimeCheckpointEnvelopeRead] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def pre_process_checkpoint(cls, data: Any) -> Any:
        if isinstance(data, AgentRunCheckpoint):
            return {
                "thread_id": data.thread_id,
                "turn_id": data.turn_id,
                "checkpoint_kind": data.checkpoint_kind,
                "runtime_snapshot": data.runtime_snapshot or {},
                "pending_client_tool_calls": data.pending_client_tool_calls or [],
                "created_at": data.created_at,
                "updated_at": data.updated_at,
            }
        return data


class AgentRunDetailRead(AgentRunSummaryRead):
    agent_instance_uuid: str
    agent_name: str
    latest_checkpoint: Optional[AgentRunCheckpointRead] = None
    events: List[AgentRunEventRead] = Field(default_factory=list)
    tool_executions: List[AgentToolExecutionRead] = Field(default_factory=list)
