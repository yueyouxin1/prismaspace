from asyncio import Task
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncGenerator, Callable, Optional, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from app.core.context import AppContext
from app.models import ResourceExecution, User, Workspace, Workflow
from app.schemas.protocol import WorkflowRuntimeEventEnvelope, WorkflowRuntimeProtocol
from app.utils.async_generator import AsyncGeneratorManager 

if TYPE_CHECKING:
    from app.engine.workflow import WorkflowRuntimePlan, WorkflowRuntimeSnapshot
    from app.services.resource.workflow.live_events import WorkflowLiveEventBuffer
    from app.services.resource.workflow.run_execution import WorkflowStreamCallbacks


@dataclass
class WorkflowRunResult:
    generator: AsyncGeneratorManager 
    trace_id: str
    run_id: str
    thread_id: str
    task: Optional[Task[Any]] = None
    cancel: Optional[Callable[[], None]] = None
    detach: Optional[Callable[[], None]] = None


class WorkflowBackgroundTaskKwargs(TypedDict):
    execution: ResourceExecution
    workflow_instance: Workflow
    runtime_plan: "WorkflowRuntimePlan"
    restored_snapshot: Optional["WorkflowRuntimeSnapshot"]
    payload: dict[str, Any]
    callbacks: "WorkflowStreamCallbacks"
    generator_manager: AsyncGeneratorManager
    external_context: "ExternalContext"
    trace_id: str
    actor: User
    live_event_buffer: Optional["WorkflowLiveEventBuffer"]


@dataclass
class PreparedWorkflowRun:
    result: WorkflowRunResult
    background_task_kwargs: WorkflowBackgroundTaskKwargs


@dataclass
class WorkflowProtocolEnvelopeStream:
    protocol: WorkflowRuntimeProtocol
    generator: AsyncGenerator[WorkflowRuntimeEventEnvelope, None]
    run_id: Optional[str] = None
    thread_id: Optional[str] = None
    trace_id: Optional[str] = None
    parent_run_id: Optional[str] = None
    task: Optional[Task[Any]] = None
    detach: Optional[Callable[[], None]] = None


class ExternalContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    app_context: AppContext = Field(..., description="运行时请求上下文")
    workflow_instance: Workflow = Field(..., description="当前工作流实例")
    runtime_workspace: Workspace = Field(..., description="运行时工作空间")
    trace_id: Optional[str] = Field(None, description="Trace ID")
    run_id: Optional[str] = Field(None, description="Execution Run ID")
    thread_id: Optional[str] = Field(None, description="Execution Thread ID")
    resume_payload: Optional[dict[str, Any]] = Field(None, description="Resume payload injected on interrupted run recovery")
