from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, Optional

from app.schemas.protocol import WorkflowRuntimeEventEnvelope, WorkflowRuntimeProtocol


class WorkflowProtocolAdapter(ABC):
    protocol: WorkflowRuntimeProtocol
    capabilities: tuple[str, ...]

    @abstractmethod
    def build_envelope(
        self,
        *,
        event_type: str,
        payload: Dict[str, Any],
        run_id: str,
        thread_id: Optional[str],
        trace_id: Optional[str],
        parent_run_id: Optional[str],
        seq: Optional[int] = None,
        ts: Optional[datetime] = None,
    ) -> WorkflowRuntimeEventEnvelope:
        ...

    @abstractmethod
    def build_session_ready(
        self,
        *,
        run_id: str,
        thread_id: Optional[str],
        trace_id: Optional[str],
        parent_run_id: Optional[str],
        mode: str,
        seq: Optional[int] = None,
    ) -> WorkflowRuntimeEventEnvelope:
        ...

    @abstractmethod
    def build_run_attached(
        self,
        *,
        run_id: str,
        thread_id: Optional[str],
        trace_id: Optional[str],
        parent_run_id: Optional[str],
        after_seq: int,
        seq: Optional[int] = None,
    ) -> WorkflowRuntimeEventEnvelope:
        ...

    @abstractmethod
    def build_replay_completed(
        self,
        *,
        run_id: str,
        thread_id: Optional[str],
        trace_id: Optional[str],
        parent_run_id: Optional[str],
        count: int,
        limit: int,
        seq: Optional[int] = None,
    ) -> WorkflowRuntimeEventEnvelope:
        ...

    @abstractmethod
    def to_sse(self, envelope: WorkflowRuntimeEventEnvelope) -> str:
        ...
