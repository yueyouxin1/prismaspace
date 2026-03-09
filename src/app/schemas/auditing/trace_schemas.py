from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class TraceSpanRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    span_uuid: str
    parent_span_uuid: Optional[str] = None
    operation_name: str
    user_id: int
    source_instance_id: Optional[int] = None
    target_instance_id: Optional[int] = None
    status: str
    duration_ms: int = 0
    self_duration_ms: int = 0
    start_offset_ms: int = 0
    end_offset_ms: int = 0
    depth: int = 0
    error_message: Optional[str] = None
    context_type: Optional[str] = None
    context_id: Optional[str] = None
    created_at: Optional[datetime] = None
    processed_at: Optional[datetime] = None
    attributes: Dict[str, Any] = Field(default_factory=dict)


class TraceTreeNodeRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    span: TraceSpanRead
    children: List["TraceTreeNodeRead"] = Field(default_factory=list)


class TraceSummaryRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    root_span_uuid: Optional[str] = None
    total_spans: int = 0
    total_duration_ms: int = 0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    status_counts: Dict[str, int] = Field(default_factory=dict)
    operation_counts: Dict[str, int] = Field(default_factory=dict)
    context_type: Optional[str] = None
    context_id: Optional[str] = None
    spans: List[TraceSpanRead] = Field(default_factory=list)


class TraceTreeRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: TraceSummaryRead
    roots: List[TraceTreeNodeRead] = Field(default_factory=list)


class TraceFlamegraphNodeRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    span_uuid: str
    value_ms: int
    self_ms: int
    start_offset_ms: int = 0
    children: List["TraceFlamegraphNodeRead"] = Field(default_factory=list)


class TraceFlamegraphRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    total_duration_ms: int = 0
    root: Optional[TraceFlamegraphNodeRead] = None


TraceTreeNodeRead.model_rebuild()
TraceFlamegraphNodeRead.model_rebuild()
