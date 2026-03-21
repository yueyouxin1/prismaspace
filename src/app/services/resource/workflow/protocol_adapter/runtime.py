from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Dict, Optional

from app.schemas.common import SSEvent
from app.schemas.protocol.workflow_runtime import (
    WORKFLOW_RUNTIME_CAPABILITIES,
    WORKFLOW_RUNTIME_SPEC,
    WorkflowRuntimeEventEnvelope,
    WorkflowRuntimeNodeRef,
)

WORKFLOW_RUNTIME_EVENT_TYPES = frozenset(
    {
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
        "session.ready",
        "system.error",
        "ui.mount",
        "ui.patch",
        "ui.unmount",
        "agent.event",
        "chat.event",
    }
)

LEGACY_WORKFLOW_RUNTIME_EVENT_TYPE_MAP = {
    "start": "run.started",
    "finish": "run.finished",
    "error": "run.failed",
    "interrupt": "run.interrupted",
    "node_start": "node.started",
    "node_finish": "node.completed",
    "node_error": "node.failed",
    "node_skipped": "node.skipped",
    "stream_start": "stream.started",
    "stream_chunk": "stream.delta",
    "stream_end": "stream.finished",
    "system_error": "system.error",
}


class WorkflowRuntimeProtocolAdapter:
    spec = WORKFLOW_RUNTIME_SPEC
    capabilities = WORKFLOW_RUNTIME_CAPABILITIES

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _resolve_node_id(payload: Dict[str, Any]) -> Optional[str]:
        if isinstance(payload.get("node_id"), str):
            return payload["node_id"]
        if isinstance(payload.get("nodeId"), str):
            return payload["nodeId"]
        interrupt = payload.get("interrupt")
        if isinstance(interrupt, dict):
            if isinstance(interrupt.get("node_id"), str):
                return interrupt["node_id"]
            if isinstance(interrupt.get("nodeId"), str):
                return interrupt["nodeId"]
        return None

    @staticmethod
    def _resolve_canonical_event_type(event_type: str, payload: Dict[str, Any]) -> str:
        if event_type in WORKFLOW_RUNTIME_EVENT_TYPES:
            return event_type
        if event_type == "finish" and str(payload.get("outcome", "")).lower() == "cancelled":
            return "run.cancelled"
        return LEGACY_WORKFLOW_RUNTIME_EVENT_TYPE_MAP.get(event_type, event_type)

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
        node_index: Optional[Dict[str, Dict[str, str]]] = None,
    ) -> WorkflowRuntimeEventEnvelope:
        canonical_type = self._resolve_canonical_event_type(event_type, payload)
        node = None
        node_id = self._resolve_node_id(payload)
        if node_id and node_index and node_id in node_index:
            node_meta = node_index[node_id]
            node = WorkflowRuntimeNodeRef(
                id=node_id,
                registryId=node_meta.get("registryId"),
                name=node_meta.get("name"),
            )
        elif node_id:
            node = WorkflowRuntimeNodeRef(id=node_id)

        return WorkflowRuntimeEventEnvelope(
            type=canonical_type,
            seq=seq,
            ts=ts or self._utcnow(),
            runId=run_id,
            threadId=thread_id,
            parentRunId=parent_run_id,
            traceId=trace_id,
            node=node,
            payload=payload or {},
        )

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
        return self.build_envelope(
            event_type="session.ready",
            payload={
                "mode": mode,
                "capabilities": list(self.capabilities),
            },
            run_id=run_id,
            thread_id=thread_id,
            trace_id=trace_id,
            parent_run_id=parent_run_id,
            seq=seq,
        )

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
        return self.build_envelope(
            event_type="run.attached",
            payload={
                "afterSeq": after_seq,
            },
            run_id=run_id,
            thread_id=thread_id,
            trace_id=trace_id,
            parent_run_id=parent_run_id,
            seq=seq,
        )

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
        return self.build_envelope(
            event_type="run.replay.completed",
            payload={
                "count": count,
                "limit": limit,
            },
            run_id=run_id,
            thread_id=thread_id,
            trace_id=trace_id,
            parent_run_id=parent_run_id,
            seq=seq,
        )

    def to_sse(self, envelope: WorkflowRuntimeEventEnvelope) -> str:
        return SSEvent(
            id=str(envelope.seq) if envelope.seq is not None else None,
            event=envelope.type,
            data=envelope.model_dump(mode="json", by_alias=True, exclude_none=True),
        ).to_sse()
