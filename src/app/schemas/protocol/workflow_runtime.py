from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Literal, Optional, Union, Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.resource.workflow.workflow_schemas import WorkflowExecutionRequest, WorkflowResumeRequest


WORKFLOW_RUNTIME_SPEC = "prismaspace.workflow.runtime/v1"
WorkflowRuntimeEventType = Literal[
    "session.ready",
    "run.started",
    "run.finished",
    "run.failed",
    "run.cancelled",
    "run.interrupted",
    "run.attached",
    "run.replay.completed",
    "node.started",
    "node.completed",
    "node.failed",
    "node.skipped",
    "stream.started",
    "stream.delta",
    "stream.finished",
    "checkpoint.created",
    "ui.mount",
    "ui.patch",
    "ui.unmount",
    "agent.event",
    "chat.event",
    "system.error",
]
WorkflowRunStatus = Literal["pending", "running", "succeeded", "failed", "cancelled", "interrupted"]
WorkflowRuntimeScopedProfile = Literal["chat-thread", "uiapp-session", "client-session"]
WorkflowRuntimeCapability = Literal[
    "cancel",
    "interrupt",
    "resume",
    "replay",
    "live_attach",
    "history",
]

WORKFLOW_RUNTIME_CAPABILITIES: tuple[WorkflowRuntimeCapability, ...] = (
    "cancel",
    "interrupt",
    "resume",
    "replay",
    "live_attach",
    "history",
)


class WorkflowRuntimeScope(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: str
    id: str


class WorkflowRuntimeNodeRef(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    registry_id: Optional[str] = Field(default=None, alias="registryId")
    name: Optional[str] = None


class WorkflowRuntimeEventEnvelope(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    spec: Literal[WORKFLOW_RUNTIME_SPEC] = WORKFLOW_RUNTIME_SPEC
    type: WorkflowRuntimeEventType | str
    seq: Optional[int] = None
    ts: datetime
    run_id: str = Field(alias="runId")
    thread_id: Optional[str] = Field(default=None, alias="threadId")
    parent_run_id: Optional[str] = Field(default=None, alias="parentRunId")
    trace_id: Optional[str] = Field(default=None, alias="traceId")
    scope: Optional[WorkflowRuntimeScope] = None
    node: Optional[WorkflowRuntimeNodeRef] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class WorkflowRuntimeBaseControlMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    spec: Literal[WORKFLOW_RUNTIME_SPEC] = WORKFLOW_RUNTIME_SPEC
    type: str
    request_id: Optional[str] = Field(default=None, alias="requestId")


class WorkflowRuntimeRunStartMessage(WorkflowRuntimeBaseControlMessage):
    type: Literal["run.start"] = "run.start"
    instance_uuid: str = Field(alias="instanceUuid")
    input: WorkflowExecutionRequest


class WorkflowRuntimeRunAttachMessage(WorkflowRuntimeBaseControlMessage):
    type: Literal["run.attach"] = "run.attach"
    run_id: str = Field(alias="runId")
    after_seq: int = Field(default=0, alias="afterSeq")


class WorkflowRuntimeRunCancelMessage(WorkflowRuntimeBaseControlMessage):
    type: Literal["run.cancel"] = "run.cancel"
    run_id: str = Field(alias="runId")


class WorkflowRuntimeRunResumeMessage(WorkflowRuntimeBaseControlMessage):
    type: Literal["run.resume"] = "run.resume"
    instance_uuid: str = Field(alias="instanceUuid")
    run_id: str = Field(alias="runId")
    resume: WorkflowResumeRequest = Field(default_factory=WorkflowResumeRequest)


class WorkflowRuntimeUiEventSubmitMessage(WorkflowRuntimeBaseControlMessage):
    type: Literal["ui.event.submit"] = "ui.event.submit"
    run_id: str = Field(alias="runId")
    interaction_id: str = Field(alias="interactionId")
    payload: Dict[str, Any] = Field(default_factory=dict)


class WorkflowRuntimeUiEventAbortMessage(WorkflowRuntimeBaseControlMessage):
    type: Literal["ui.event.abort"] = "ui.event.abort"
    run_id: str = Field(alias="runId")
    interaction_id: str = Field(alias="interactionId")


class WorkflowRuntimeActiveRunResolveMessage(WorkflowRuntimeBaseControlMessage):
    type: Literal["active-run.resolve"] = "active-run.resolve"
    scope: WorkflowRuntimeScope
    profile: WorkflowRuntimeScopedProfile


WorkflowRuntimeControlMessage = Annotated[
    Union[
        WorkflowRuntimeRunStartMessage,
        WorkflowRuntimeRunAttachMessage,
        WorkflowRuntimeRunCancelMessage,
        WorkflowRuntimeRunResumeMessage,
        WorkflowRuntimeUiEventSubmitMessage,
        WorkflowRuntimeUiEventAbortMessage,
        WorkflowRuntimeActiveRunResolveMessage,
    ],
    Field(discriminator="type"),
]
