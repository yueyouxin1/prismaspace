# src/app/schemas/resource/agent/agent_schemas.py

from enum import Enum
from pydantic import BaseModel, Field, ConfigDict, model_validator, conint, confloat
from typing import Literal, Union, Dict, List, Any, Optional
from app.schemas.resource.resource_schemas import InstanceUpdate, InstanceRead
from app.schemas.resource.knowledge.knowledge_schemas import RAGConfig
from app.schemas.common import SSEvent, ExecutionRequest, ExecutionResponse
from app.engine.model.llm import LLMMessage
from app.engine.agent import AgentResult

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

class AgentEvent(SSEvent):
    """Agent 运行时产生的原子事件"""
    event: Literal["start", "think", "chunk", "tool_input", "tool_output", "finish", "cancel", "error"]

class AgentExecutionInputs(BaseModel):
    input_query: str = Field(..., description="用户的最新输入")
    session_uuid: Optional[str] = Field(None, description="会话UUID。若提供则进行有状态对话。")
    # history 仅用于无状态调用的补充，session_uuid 优先
    history: Optional[List[LLMMessage]] = Field(None, description="无状态模式下的历史消息")

class AgentExecutionRequest(ExecutionRequest):
    inputs: AgentExecutionInputs

class AgentExecutionResponseData(BaseModel):
    agent_result: Optional[AgentResult] = Field(None)
    session_uuid: Optional[str] = Field(None)
    trace_id: Optional[str] = Field(None)

class AgentExecutionResponse(ExecutionResponse):
    data: AgentExecutionResponseData