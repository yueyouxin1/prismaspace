# src/app/services/resource/agent/types/agent.py

from dataclasses import dataclass
from asyncio import Task
from typing import TYPE_CHECKING, Any, Callable, Optional, TypedDict

from app.schemas.resource.agent.agent_schemas import AgentConfig
from app.utils.async_generator import AsyncGeneratorManager

if TYPE_CHECKING:
    from app.core.trace_manager import TraceManager
    from app.engine.agent import AgentRuntimeCheckpoint
    from app.models import ResourceExecution, ResourceRef, ServiceModuleVersion, Workspace
    from app.models.resource.agent import Agent
    from app.schemas.protocol import RunAgentInputExt
    from app.services.resource.agent.agent_session_manager import AgentSessionManager
    from app.services.resource.agent.live_events import AgentLiveEventBuffer
    from app.services.resource.agent.processors import ResourceAwareToolExecutor
    from app.services.resource.agent.protocol_adapter.base import ProtocolAdaptedRun


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


class AgentBackgroundTaskKwargs(TypedDict):
    agent_config: AgentConfig
    llm_module_version: "ServiceModuleVersion"
    runtime_workspace: "Workspace"
    trace_manager: "TraceManager"
    generator_manager: AsyncGeneratorManager
    execution: "ResourceExecution"
    turn_id: str
    session_manager: Optional["AgentSessionManager"]
    run_input: Optional["RunAgentInputExt"]
    message_ids: Optional[AgentStreamMessageIds]
    dependencies: Optional[list["ResourceRef"]]
    adapted: Optional["ProtocolAdaptedRun"]
    tool_executor: Optional["ResourceAwareToolExecutor"]
    agent_instance: Optional["Agent"]
    live_event_buffer: Optional["AgentLiveEventBuffer"]
    resume_checkpoint: Optional["AgentRuntimeCheckpoint"]


@dataclass
class PreparedAgentRun:
    result: AgentRunResult
    background_task_kwargs: AgentBackgroundTaskKwargs
