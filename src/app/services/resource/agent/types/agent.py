# src/app/services/resource/agent/types/agent.py

from dataclasses import dataclass
from asyncio import Task
from typing import Any, Callable, Optional

from app.schemas.resource.agent.agent_schemas import AgentConfig
from app.utils.async_generator import AsyncGeneratorManager


@dataclass(frozen=True)
class AgentStreamMessageIds:
    user_message_id: str
    assistant_message_id: str
    reasoning_message_id: str
    activity_message_id: str

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
    run_id: str
    turn_id: str
    trace_id: Optional[str]
    thread_id: str
    cancel: Optional[Callable[[], None]] = None
    detach: Optional[Callable[[], None]] = None
    task: Optional[Task[Any]] = None


@dataclass
class PreparedAgentRun:
    result: AgentRunResult
    background_task_kwargs: dict[str, Any]
