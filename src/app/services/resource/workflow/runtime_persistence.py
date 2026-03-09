from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, Iterable, Optional

from app.core.context import AppContext
from app.engine.workflow import (
    NodeResultData,
    NodeState,
    WorkflowRuntimeNodeSpec,
    WorkflowRuntimePlan,
    WorkflowRuntimeSnapshot,
)
from app.models import ResourceExecution, Workflow
from app.models.resource.workflow import (
    WorkflowCheckpointReason,
    WorkflowExecutionCheckpoint,
    WorkflowNodeExecution,
)
from app.services.base_service import BaseService
from app.dao.resource.workflow.workflow_runtime_dao import (
    WorkflowExecutionCheckpointDao,
    WorkflowNodeExecutionDao,
)


class WorkflowRuntimePersistenceService(BaseService):
    def __init__(self, context: AppContext):
        self.context = context
        self.db = context.db
        self.checkpoint_dao = WorkflowExecutionCheckpointDao(self.db)
        self.node_execution_dao = WorkflowNodeExecutionDao(self.db)

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(UTC).replace(tzinfo=None)

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, dict):
            return {key: WorkflowRuntimePersistenceService._jsonable(val) for key, val in value.items()}
        if isinstance(value, list):
            return [WorkflowRuntimePersistenceService._jsonable(item) for item in value]
        if isinstance(value, tuple):
            return [WorkflowRuntimePersistenceService._jsonable(item) for item in value]
        if hasattr(value, "model_dump"):
            return WorkflowRuntimePersistenceService._jsonable(
                value.model_dump(mode="json", by_alias=True, exclude_none=False)
            )
        return json.loads(json.dumps(value, default=str, ensure_ascii=False))

    async def upsert_node_execution(
        self,
        *,
        execution: ResourceExecution,
        workflow_instance: Workflow,
        node: WorkflowRuntimeNodeSpec,
        state: NodeState,
        reason: str,
        attempt: int = 1,
    ) -> WorkflowNodeExecution:
        record = await self.node_execution_dao.get_by_execution_node_attempt(
            resource_execution_id=execution.id,
            node_id=node.id,
            attempt=attempt,
        )
        if record is None:
            record = WorkflowNodeExecution(
                resource_execution_id=execution.id,
                workflow_instance_id=workflow_instance.id,
                node_id=node.id,
                node_name=node.name,
                node_type=node.registry_id,
                attempt=attempt,
            )
            self.db.add(record)

        now = self._utcnow()
        record.status = state.status
        record.input = self._jsonable(state.input) if state.input else None
        record.result = self._jsonable(state.result) if state.result else None
        record.error_message = state.result.error_msg if state.result else None
        record.activated_port = state.activated_port
        record.executed_time = state.executed_time

        if reason == "node_start" and record.started_at is None:
            record.started_at = now
        elif reason == "node_streamtask":
            if record.started_at is None:
                record.started_at = now
        elif reason in {"node_completed", "node_failed", "node_skipped"}:
            if record.started_at is None:
                record.started_at = now
            record.finished_at = now

        await self.db.flush()
        return record

    async def create_checkpoint(
        self,
        *,
        execution: ResourceExecution,
        workflow_instance: Workflow,
        runtime_plan: WorkflowRuntimePlan,
        snapshot: WorkflowRuntimeSnapshot,
        reason: WorkflowCheckpointReason,
        node_id: Optional[str] = None,
    ) -> WorkflowExecutionCheckpoint:
        checkpoint = WorkflowExecutionCheckpoint(
            resource_execution_id=execution.id,
            workflow_instance_id=workflow_instance.id,
            step_index=snapshot.step_index,
            reason=reason,
            node_id=node_id,
            runtime_plan=self._jsonable(runtime_plan),
            payload=self._jsonable(snapshot.payload) or {},
            variables=self._jsonable(snapshot.variables) or {},
            node_states=self._jsonable(snapshot.node_states) or {},
            ready_queue=self._jsonable(snapshot.ready_queue) or [],
        )
        self.db.add(checkpoint)
        await self.db.flush()
        return checkpoint

    async def get_latest_checkpoint(
        self,
        *,
        execution_id: int,
    ) -> Optional[WorkflowExecutionCheckpoint]:
        return await self.checkpoint_dao.get_latest_by_execution_id(execution_id)

    def build_resume_snapshot(
        self,
        *,
        checkpoint: WorkflowExecutionCheckpoint,
        runtime_plan: WorkflowRuntimePlan,
    ) -> WorkflowRuntimeSnapshot:
        snapshot = WorkflowRuntimeSnapshot.model_validate(
            {
                "payload": checkpoint.payload or {},
                "variables": checkpoint.variables or {},
                "node_states": checkpoint.node_states or {},
                "ready_queue": checkpoint.ready_queue or [],
                "step_index": checkpoint.step_index,
            }
        )

        normalized_states: Dict[str, NodeState] = {}
        for node in runtime_plan.all_nodes:
            state = snapshot.node_states.get(node.id, NodeState(node_id=node.id))
            if not isinstance(state, NodeState):
                state = NodeState.model_validate(state)

            if state.status in {"RUNNING", "STREAMTASK", "STREAMSTART", "STREAMING", "FAILED", "INTERRUPTED"}:
                state = state.model_copy(
                    update={
                        "status": "PENDING",
                        "input": {},
                        "result": NodeResultData(),
                        "activated_port": "0",
                        "executed_time": 0.0,
                    }
                )
            normalized_states[node.id] = state

        return WorkflowRuntimeSnapshot(
            payload=dict(snapshot.payload or {}),
            variables=dict(snapshot.variables or {}),
            node_states=normalized_states,
            ready_queue=self._rebuild_ready_queue(
                runtime_plan=runtime_plan,
                node_states=normalized_states,
                persisted_queue=snapshot.ready_queue or [],
            ),
            version=snapshot.version,
            step_index=snapshot.step_index,
        )

    def _rebuild_ready_queue(
        self,
        *,
        runtime_plan: WorkflowRuntimePlan,
        node_states: Dict[str, NodeState],
        persisted_queue: Iterable[str],
    ) -> list[str]:
        queue: list[str] = []
        for node_id in persisted_queue:
            state = node_states.get(node_id)
            if state and state.status == "PENDING" and node_id not in queue:
                queue.append(node_id)

        ready_states = {"COMPLETED", "SKIPPED", "STREAMTASK"}
        for node in runtime_plan.all_nodes:
            state = node_states.get(node.id)
            if state is None or state.status != "PENDING" or node.id in queue:
                continue

            predecessors = runtime_plan.get_predecessors(node.id)
            if not predecessors:
                queue.append(node.id)
                continue

            if any(node_states.get(pred, NodeState(node_id=pred)).status not in ready_states for pred in predecessors):
                continue

            is_active = False
            for pred_id in predecessors:
                pred_state = node_states[pred_id]
                if pred_state.status in {"COMPLETED", "STREAMTASK"}:
                    targets = runtime_plan.get_targets_from_port(pred_id, pred_state.activated_port)
                    if node.id in targets:
                        is_active = True
                        break

            if is_active:
                queue.append(node.id)

        return queue


class WorkflowDurableRuntimeObserver:
    CANCEL_SIGNAL_TTL = timedelta(hours=24)

    def __init__(
        self,
        *,
        context: AppContext,
        execution: ResourceExecution,
        workflow_instance: Workflow,
        runtime_plan: WorkflowRuntimePlan,
    ):
        self.context = context
        self.execution = execution
        self.workflow_instance = workflow_instance
        self.runtime_plan = runtime_plan
        self.persistence = WorkflowRuntimePersistenceService(context)

    @staticmethod
    def cancel_signal_key(run_id: str) -> str:
        return f"workflow:run:{run_id}:cancel"

    async def request_cancel(self) -> None:
        await self.context.redis_service.set_json(
            self.cancel_signal_key(self.execution.run_id),
            {"requested_at": self.persistence._utcnow().isoformat()},
            expire=self.CANCEL_SIGNAL_TTL,
        )

    async def clear_cancel_signal(self) -> None:
        await self.context.redis_service.delete_key(self.cancel_signal_key(self.execution.run_id))

    async def should_cancel(self) -> bool:
        payload = await self.context.redis_service.get_json(self.cancel_signal_key(self.execution.run_id))
        return payload is not None

    async def on_execution_start(
        self,
        workflow_plan: WorkflowRuntimePlan,
        snapshot: WorkflowRuntimeSnapshot,
    ) -> None:
        await self.persistence.create_checkpoint(
            execution=self.execution,
            workflow_instance=self.workflow_instance,
            runtime_plan=workflow_plan,
            snapshot=snapshot,
            reason=WorkflowCheckpointReason.EXECUTION_START,
        )
        await self.context.db.commit()

    async def on_execution_end(
        self,
        result: Optional[NodeResultData],
        snapshot: WorkflowRuntimeSnapshot,
        status: str,
    ) -> None:
        reason = {
            "succeeded": WorkflowCheckpointReason.EXECUTION_SUCCEEDED,
            "failed": WorkflowCheckpointReason.EXECUTION_FAILED,
            "interrupted": WorkflowCheckpointReason.EXECUTION_INTERRUPTED,
            "cancelled": WorkflowCheckpointReason.EXECUTION_CANCELLED,
        }.get(status, WorkflowCheckpointReason.EXECUTION_FAILED)
        await self.persistence.create_checkpoint(
            execution=self.execution,
            workflow_instance=self.workflow_instance,
            runtime_plan=self.runtime_plan,
            snapshot=snapshot,
            reason=reason,
        )
        await self.clear_cancel_signal()
        await self.context.db.commit()

    async def on_node_state(
        self,
        node: WorkflowRuntimeNodeSpec,
        state: NodeState,
        reason: str,
        snapshot: Optional[WorkflowRuntimeSnapshot] = None,
    ) -> None:
        await self.persistence.upsert_node_execution(
            execution=self.execution,
            workflow_instance=self.workflow_instance,
            node=node,
            state=state,
            reason=reason,
        )

        checkpoint_reason = {
            "node_completed": WorkflowCheckpointReason.NODE_COMPLETED,
            "node_failed": WorkflowCheckpointReason.NODE_FAILED,
            "node_interrupted": WorkflowCheckpointReason.NODE_INTERRUPTED,
            "node_skipped": WorkflowCheckpointReason.NODE_SKIPPED,
        }.get(reason)
        if checkpoint_reason is not None and snapshot is not None:
            await self.persistence.create_checkpoint(
                execution=self.execution,
                workflow_instance=self.workflow_instance,
                runtime_plan=self.runtime_plan,
                snapshot=snapshot,
                reason=checkpoint_reason,
                node_id=node.id,
            )
            await self.context.db.commit()
