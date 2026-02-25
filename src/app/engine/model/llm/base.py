# src/app/engine/model/llm/base.py

from abc import ABC, abstractmethod
from typing import Dict, Any, Literal, List, Optional, Union, AsyncGenerator
from pydantic import BaseModel, Field, HttpUrl

# --- 数据模型 (Data Models) ---

class LLMProviderConfig(BaseModel):
    """
    定义了调用模型所需的客户端和凭证信息。
    """
    client_name: str = Field(..., description="客户端的唯一标识符, e.g., 'openai', 'azure', 'dashscope'")
    api_key: str = Field(..., description="API Key")
    base_url: Optional[HttpUrl] = Field(None, description="API的基础URL，用于代理或私有部署")
    timeout: int = Field(60, description="API请求的超时时间（秒）")
    max_retries: int = Field(2, description="API请求的最大重试次数")

class LLMToolFunction(BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any] # JSON Schema

class LLMTool(BaseModel):
    type: Literal["function"] = "function"
    function: LLMToolFunction

class LLMMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None # 模型生成的 tool_calls
    tool_call_id: Optional[str] = None # role='tool' 时需要

class LLMRunConfig(BaseModel):
    """定义单次模型运行的配置"""
    model: str
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    top_p: float = Field(1.0, ge=0.0, le=1.0)
    presence_penalty: float = Field(0.0, ge=-2.0, le=2.0)
    frequency_penalty: float = Field(0.0, ge=-2.0, le=2.0)
    max_context_window: Optional[int] = Field(None, description="上下文的最大token数，用于成本控制和防超长")
    max_tokens: int = Field(default=2048, ge=1, description="生成的最大token数")
    enable_thinking: bool = Field(default=False, description="深度思考开关 (如支持)")
    thinking_budget: Optional[int] = Field(None, ge=1, description="最大思考长度 (仅当开启深度思考时有效)")
    stream: bool = True
    response_format: Optional[Dict[str, Any]] = Field(
        default_factory=lambda: {"type": "text"},
        description="""返回内容的格式。支持三种模式：
        1. 文本模式: {"type": "text"}
        2. JSON Object模式: {"type": "json_object"} (需在提示词中包含JSON关键词)
        3. JSON Schema模式: {"type": "json_schema", "json_schema": {...}, "strict": true/false}
        """
    )
    tools: Optional[List[LLMTool]] = None
    tool_choice: Optional[Union[Literal["auto", "none"], Dict]] = "auto"

class LLMUsage(BaseModel):
    """标准化的用量统计"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

class LLMToolCall(BaseModel):
    """标准化的工具调用请求结构"""
    id: str
    type: Literal["function"] = "function"
    function: Dict[str, str] # e.g., {"name": "get_weather", "arguments": '{"location": "beijing"}'}

class LLMResult(BaseModel):
    """标准化的最终返回结果"""
    message: LLMMessage
    usage: LLMUsage

# --- 引擎层异常 ---

class LLMEngineError(Exception):
    """LLM引擎所有错误的基类"""
    pass

class LLMAuthenticationError(LLMEngineError):
    """凭证无效或权限不足"""
    pass

class LLMRateLimitError(LLMEngineError):
    """达到API频率限制"""
    pass

class LLMContextLengthExceededError(LLMEngineError):
    """上下文长度超过模型限制"""
    pass

class LLMBadRequestError(LLMEngineError):
    """请求参数无效（非上下文长度问题）"""
    pass

class LLMProviderNotFoundError(LLMEngineError):
    """当找不到指定的 LLM provider 时抛出"""
    pass
    
# --- 回调接口 (Callback) ---

class LLMEngineCallbacks(ABC):
    """
    定义了LLM引擎在执行过程中向外报告事件的接口。
    业务逻辑层需要实现这个接口，并将其注入引擎。
    """
    @abstractmethod
    async def on_start(self) -> None:
        """在生成开始时调用。"""
        ...

    @abstractmethod
    async def on_chunk_generated(self, chunk: str) -> None:
        """每当生成一个新的文本块时调用（流式模式）。"""
        ...
    
    @abstractmethod
    async def on_tool_calls_generated(self, tool_calls: List[LLMToolCall]) -> None:
        """当模型决定调用工具时调用。"""
        ...

    @abstractmethod
    async def on_success(self, result: LLMResult) -> None:
        """在整个生成过程成功完成时调用。"""
        ...
        
    @abstractmethod
    async def on_error(self, error: Exception) -> None:
        """在发生错误时调用。"""
        ...
    
    @abstractmethod
    async def on_usage(self, usage: LLMUsage) -> None:
        """在生成结束后，报告本次调用的token用量。"""
        ...

    @abstractmethod
    async def on_cancel(self, result: LLMResult) -> None:
        """当生成任务被外部中止时调用。"""
        ...

# --- 客户端接口 (Client Interface - Strategy Pattern) ---

class BaseLLMClient(ABC):
    """
    所有具体SDK实现的统一接口。
    LLMEngineService将与此接口交互，而不是具体的实现类。
    """
    def __init__(self, config: LLMProviderConfig):
        ...

    @abstractmethod
    async def generate(
        self,
        run_config: LLMRunConfig,
        messages: List[LLMMessage],
        callbacks: Optional[LLMEngineCallbacks] = None,
    ) -> None:
        """
        执行模型生成的核心方法。
        注意：此方法不直接返回结果，而是通过回调函数向外通知状态。
        """
        ...