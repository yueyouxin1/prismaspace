from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class RuntimeCheckpointSummaryRead(BaseModel):
    model_config = ConfigDict(extra="allow")

    message_count: Optional[int] = None
    tool_count: Optional[int] = None
    pending_client_tool_call_count: Optional[int] = None
    step_count: Optional[int] = None
    next_iteration: Optional[int] = None
    step_index: Optional[int] = None
    ready_queue_size: Optional[int] = None
    node_state_count: Optional[int] = None


class RuntimeCheckpointEnvelopeRead(BaseModel):
    """
    统一资源运行态 checkpoint 协议。
    用作跨 Agent / Workflow / 后续其他 runtime 的稳定观察面。
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    resource_type: str
    engine: str
    checkpoint_kind: str
    phase: Optional[str] = None
    reason: Optional[str] = None

    run_id: str
    thread_id: str
    parent_run_id: Optional[str] = None
    trace_id: Optional[str] = None

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    summary: RuntimeCheckpointSummaryRead = Field(default_factory=RuntimeCheckpointSummaryRead)
    state: Dict[str, Any] = Field(default_factory=dict)
