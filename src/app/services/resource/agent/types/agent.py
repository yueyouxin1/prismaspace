# src/app/services/resource/agent/types/agent.py

from dataclasses import dataclass
from app.utils.async_generator import AsyncGeneratorManager 
from app.schemas.resource.agent.agent_schemas import AgentConfig

@dataclass
class AgentRunResult:
    """
    专门用于承载 Agent 启动后的返回结果。
    它包含了两部分：
    1. stream: 用于接收异步数据的管道
    2. meta: 这一运行实例的静态上下文（配置、TraceID等）
    """
    generator: AsyncGeneratorManager 
    config: AgentConfig
    trace_id: str