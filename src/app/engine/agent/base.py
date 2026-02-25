# src/app/engine/agent/base.py

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from ..model.llm.base import LLMRunConfig, LLMMessage, LLMTool, LLMToolCall, LLMUsage

# --- Agent 引擎的输入输出模型 ---
class AgentInput(BaseModel):
    messages: List[LLMMessage]
    # ... 未来可以添加更多配置，如 user_id, session_id 等

class AgentStep(BaseModel):
    """代表 Agent 思维链中的一个步骤，用于可观察性"""
    thought: Optional[str] = Field(None, description="模型的思考过程")
    action: LLMToolCall = Field(..., description="模型决定执行的动作")
    observation: Any = Field(..., description="执行动作后返回的观察结果")

class AgentResult(BaseModel):
    """Agent 引擎的最终输出"""
    message: LLMMessage
    steps: List[AgentStep] = Field([], description="完整的思维链步骤")
    usage: LLMUsage = Field(default_factory=LLMUsage, description="整个Agent执行过程中的总Token用量")

# --- Agent 引擎的回调协议 ---
class AgentEngineCallbacks(ABC):
    """
    定义了 Agent 引擎向外报告事件的接口。
    """
    @abstractmethod
    async def on_agent_start(self) -> None:
        ...

    @abstractmethod
    async def on_tool_calls_generated(self, tool_calls: List[LLMToolCall]) -> None:
        """当模型决定调用工具时调用。"""
        ...

    @abstractmethod
    async def on_agent_step(self, step: AgentStep) -> None:
        """当 Agent 完成一个完整的 思维-行动-观察 步骤时调用"""
        ...
    
    @abstractmethod
    async def on_final_chunk_generated(self, chunk: str) -> None:
        """当最终答案的文本块生成时调用（流式）"""
        ...
    
    @abstractmethod
    async def on_agent_finish(self, result: AgentResult) -> None:
        ...
    
    @abstractmethod
    async def on_agent_cancel(self, result: AgentResult) -> None:
        ...

    @abstractmethod
    async def on_agent_error(self, error: Exception) -> None:
        ...

    @abstractmethod
    async def on_usage(self, usage: LLMUsage) -> None:
        """在生成结束后，报告本次调用的token用量。"""
        ...
        
# --- Agent 引擎的插件化执行器协议 ---
class BaseToolExecutor(ABC):
    """
    工具执行器的协议
    """
    @abstractmethod
    async def execute(self, tool_name: str, tool_args: Dict[str, Any]) -> Any:
        ...
    
    @abstractmethod
    def get_llm_tools(self) -> List[LLMTool]:
        ...