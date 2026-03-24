from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.engine.workflow import NodeResultData, NodeState, StreamEvent, WorkflowCallbacks, WorkflowRuntimePlan
from app.schemas.resource.workflow.workflow_schemas import WorkflowEvent
from app.utils.async_generator import AsyncGeneratorManager


logger = logging.getLogger(__name__)


class PersistingWorkflowCallbacks(WorkflowCallbacks):
    """
    Workflow runtime callbacks aligned with the Agent hot path model:
    emit to the live stream immediately, capture events in memory, and
    persist them in batch after the run reaches a terminal state.
    """

    DURABLE_EVENT_TYPES = {
        "run.started",
        "run.finished",
        "run.failed",
        "run.cancelled",
        "run.interrupted",
        "node.started",
        "node.completed",
        "node.failed",
        "node.skipped",
        "stream.started",
        "stream.finished",
        "checkpoint.created",
        "interrupt",
        "system.error",
    }

    def __init__(
        self,
        *,
        generator_manager: AsyncGeneratorManager,
        trace_id: str,
        run_id: str,
        thread_id: str,
        event_sink: Optional[Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = None,
    ):
        self.generator_manager = generator_manager
        self.trace_id = trace_id
        self.run_id = run_id
        self.thread_id = thread_id
        self.event_sink = event_sink
        self._node_meta: Dict[str, Dict[str, str]] = {}
        self._captured_events: List[Dict[str, Any]] = []

    def _bind_runtime_plan(self, workflow_plan: WorkflowRuntimePlan) -> None:
        self._node_meta = {
            node.id: {
                "id": node.id,
                "registryId": node.registry_id,
                "name": node.name,
            }
            for node in workflow_plan.all_nodes
        }

    def _enrich_with_node(self, payload: Dict[str, Any], node_id: Optional[str]) -> Dict[str, Any]:
        if not node_id:
            return payload
        node_meta = self._node_meta.get(node_id)
        if not node_meta:
            return payload
        enriched = dict(payload)
        enriched.setdefault("node", node_meta)
        return enriched

    async def _emit(self, event: WorkflowEvent) -> None:
        try:
            envelope_payload = {
                "event": event.event,
                "data": event.data,
            }
            if event.event in self.DURABLE_EVENT_TYPES:
                self._captured_events.append(
                    {
                        "event_type": event.event,
                        "payload": event.data,
                    }
                )
            if self.event_sink is not None:
                envelope = await self.event_sink(envelope_payload)
                if isinstance(envelope, dict) and envelope.get("seq") is not None:
                    event.id = str(envelope["seq"])
            self.generator_manager.put_nowait(event)
        except Exception as exc:
            logger.error("Failed to put workflow event to queue: %s", exc)

    def get_captured_events(self) -> List[Dict[str, Any]]:
        return list(self._captured_events)

    async def on_execution_start(self, workflow_def: WorkflowRuntimePlan) -> None:
        self._bind_runtime_plan(workflow_def)
        await self._emit(
            WorkflowEvent(
                event="run.started",
                data={
                    "trace_id": self.trace_id,
                    "run_id": self.run_id,
                    "thread_id": self.thread_id,
                },
            )
        )

    async def on_node_start(self, state: NodeState) -> None:
        payload = self._enrich_with_node(state.model_dump(), state.node_id)
        await self._emit(WorkflowEvent(event="node.started", data=payload))

    async def on_node_finish(self, state: NodeState) -> None:
        payload = self._enrich_with_node(state.model_dump(), state.node_id)
        await self._emit(WorkflowEvent(event="node.completed", data=payload))

    async def on_node_error(self, state: NodeState) -> None:
        payload = self._enrich_with_node(state.model_dump(), state.node_id)
        await self._emit(WorkflowEvent(event="node.failed", data=payload))

    async def on_node_skipped(self, state: NodeState) -> None:
        payload = self._enrich_with_node(state.model_dump(), state.node_id)
        await self._emit(WorkflowEvent(event="node.skipped", data=payload))

    async def on_stream_start(self, event: StreamEvent) -> None:
        payload = self._enrich_with_node(event.model_dump(), event.node_id)
        await self._emit(WorkflowEvent(event="stream.started", data=payload))

    async def on_stream_chunk(self, event: StreamEvent) -> None:
        payload = self._enrich_with_node(event.model_dump(), event.node_id)
        await self._emit(WorkflowEvent(event="stream.delta", data=payload))

    async def on_stream_end(self, event: StreamEvent) -> None:
        payload = self._enrich_with_node(event.model_dump(), event.node_id)
        await self._emit(WorkflowEvent(event="stream.finished", data=payload))

    async def on_execution_end(self, result: NodeResultData) -> None:
        payload = result.model_dump(mode="json")
        payload.update({"run_id": self.run_id, "thread_id": self.thread_id})
        await self._emit(WorkflowEvent(event="run.finished", data=payload))

    async def on_event(self, type: str, data: Any) -> None:
        payload = data if isinstance(data, dict) else {"detail": data}
        payload.setdefault("run_id", self.run_id)
        payload.setdefault("thread_id", self.thread_id)
        await self._emit(WorkflowEvent(event=type, data=payload))
