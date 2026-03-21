from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from app.models import ResourceExecutionStatus
from app.schemas.resource.workflow.workflow_schemas import (
    WorkflowEventRead,
    WorkflowExecutionRequest,
    WorkflowInterruptRead,
    WorkflowRunNodeRead,
    WorkflowRunRead,
    WorkflowRunSummaryRead,
)
from app.services.exceptions import NotFoundError, ServiceException


logger = logging.getLogger(__name__)


class WorkflowRunQueryService:
    """
    负责 workflow run 查询、事件查询与 resume 载荷解析。
    """

    def __init__(self, workflow_service):
        self.workflow_service = workflow_service

    def build_run_summary(self, execution, latest_checkpoint) -> WorkflowRunSummaryRead:
        service = self.workflow_service
        status_value = execution.status.value if hasattr(execution.status, "value") else str(execution.status)
        return WorkflowRunSummaryRead(
            run_id=execution.run_id,
            thread_id=execution.thread_id,
            parent_run_id=execution.parent_run_id,
            status=status_value,
            trace_id=execution.trace_id,
            error_code=execution.error_code,
            error_message=execution.error_message,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            latest_checkpoint=service.runtime_persistence.build_checkpoint_read(execution=execution, checkpoint=latest_checkpoint) if latest_checkpoint else None,
        )

    async def get_latest_interrupt(
        self,
        *,
        execution_id: int,
    ) -> Optional[WorkflowInterruptRead]:
        service = self.workflow_service
        latest_interrupt = await service.event_log_service.get_latest_event(
            execution_id=execution_id,
            event_type="run.interrupted",
        )
        if latest_interrupt is None:
            latest_interrupt = await service.event_log_service.get_latest_event(
                execution_id=execution_id,
                event_type="interrupt",
            )
        if latest_interrupt is None or not isinstance(latest_interrupt.payload, dict):
            return None

        interrupt_payload = latest_interrupt.payload.get("interrupt")
        if not isinstance(interrupt_payload, dict):
            return None

        try:
            return WorkflowInterruptRead.model_validate(interrupt_payload)
        except ValidationError:
            logger.warning("Invalid persisted workflow interrupt payload for execution %s", execution_id, exc_info=True)
            return None

    async def resolve_resume_payload(
        self,
        *,
        parent_execution,
        execute_params: WorkflowExecutionRequest,
    ) -> Any:
        if execute_params.resume is None:
            return execute_params.meta.get("resume") if isinstance(execute_params.meta, dict) else None

        resume_token = execute_params.resume.token
        if resume_token is not None:
            if resume_token.run_id != parent_execution.run_id:
                raise ServiceException("Resume token run mismatch.")
            if resume_token.thread_id != parent_execution.thread_id:
                raise ServiceException("Resume token thread mismatch.")

        resume_payload = execute_params.resume.output
        interrupt = await self.get_latest_interrupt(execution_id=parent_execution.id)
        resume_key = None
        if interrupt is not None and isinstance(interrupt.payload, dict):
            payload_resume_key = interrupt.payload.get("resumeOutputKey")
            if isinstance(payload_resume_key, str) and payload_resume_key.strip():
                resume_key = payload_resume_key.strip()
            interrupt_token = interrupt.resume_token
            if resume_token is not None and interrupt_token is not None and interrupt_token.node_id != resume_token.node_id:
                raise ServiceException("Resume token node mismatch.")

        if resume_key:
            return {resume_key: resume_payload}
        return resume_payload

    async def get_run(self, run_id: str) -> WorkflowRunRead:
        service = self.workflow_service
        execution = await service.execution_ledger_service.get_by_run_id(run_id)
        if execution is None:
            raise NotFoundError("Workflow run not found.")

        workflow_stub = await service.dao.get_by_pk(execution.resource_instance_id)
        if workflow_stub is None:
            raise NotFoundError("Workflow instance not found.")
        workflow_instance = await service.get_by_uuid(workflow_stub.uuid)
        if workflow_instance is None:
            raise NotFoundError("Workflow instance not found.")

        await service._check_execute_perm(workflow_instance)

        latest_checkpoint = await service.runtime_persistence.get_latest_checkpoint(execution_id=execution.id)
        node_executions = await service.runtime_persistence.node_execution_dao.get_list(
            where={"resource_execution_id": execution.id},
            order=["id"],
        )
        interrupt = await self.get_latest_interrupt(execution_id=execution.id)

        status_value = execution.status.value if hasattr(execution.status, "value") else str(execution.status)
        can_resume = status_value in {
            ResourceExecutionStatus.FAILED.value,
            ResourceExecutionStatus.CANCELLED.value,
            ResourceExecutionStatus.INTERRUPTED.value,
        } and latest_checkpoint is not None

        return WorkflowRunRead(
            run_id=execution.run_id,
            thread_id=execution.thread_id,
            parent_run_id=execution.parent_run_id,
            status=status_value,
            trace_id=execution.trace_id,
            error_code=execution.error_code,
            error_message=execution.error_message,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            workflow_instance_uuid=workflow_instance.uuid,
            workflow_name=workflow_instance.name,
            latest_checkpoint=service.runtime_persistence.build_checkpoint_read(execution=execution, checkpoint=latest_checkpoint) if latest_checkpoint else None,
            node_executions=[WorkflowRunNodeRead.model_validate(item) for item in node_executions],
            can_resume=can_resume,
            interrupt=interrupt,
        )

    async def list_run_events(
        self,
        run_id: str,
        *,
        limit: int = 1000,
    ) -> List[WorkflowEventRead]:
        service = self.workflow_service
        execution = await service.execution_ledger_service.get_by_run_id(run_id)
        if execution is None:
            raise NotFoundError("Workflow run not found.")

        workflow_stub = await service.dao.get_by_pk(execution.resource_instance_id)
        if workflow_stub is None:
            raise NotFoundError("Workflow instance not found.")
        workflow_instance = await service.get_by_uuid(workflow_stub.uuid)
        if workflow_instance is None:
            raise NotFoundError("Workflow instance not found.")
        await service._check_execute_perm(workflow_instance)

        return await service.event_log_service.list_events(
            execution_id=execution.id,
            limit=limit,
        )

    async def stream_live_run_events(
        self,
        run_id: str,
        *,
        after_seq: int = 0,
    ):
        service = self.workflow_service
        execution = await service.execution_ledger_service.get_by_run_id(run_id)
        if execution is None:
            raise NotFoundError("Workflow run not found.")

        workflow_stub = await service.dao.get_by_pk(execution.resource_instance_id)
        if workflow_stub is None:
            raise NotFoundError("Workflow instance not found.")
        workflow_instance = await service.get_by_uuid(workflow_stub.uuid)
        if workflow_instance is None:
            raise NotFoundError("Workflow instance not found.")
        await service._check_execute_perm(workflow_instance)

        current_seq = after_seq
        seen_terminal_event = False
        async for envelope in service.live_event_service.stream_events(run_id, after_seq=after_seq):
            try:
                current_seq = max(current_seq, int(envelope.get("seq", current_seq)))
            except Exception:
                pass
            payload = envelope.get("payload", {})
            if isinstance(payload, dict) and str(payload.get("event", "")) in {"run.finished", "run.failed", "run.interrupted", "run.cancelled", "system.error"}:
                seen_terminal_event = True
            yield envelope

        for event in await service.event_log_service.list_events_after_sequence(
            execution_id=execution.id,
            after_sequence_no=current_seq,
            limit=1000,
        ):
            if event.event_type in {"run.finished", "run.failed", "run.interrupted", "run.cancelled", "system.error"}:
                seen_terminal_event = True
            yield {
                "seq": event.sequence_no,
                "payload": {
                    "event": event.event_type,
                    "data": event.payload,
                },
            }

        if not seen_terminal_event:
            for _ in range(50):
                await service.db.refresh(execution)

                for event in await service.event_log_service.list_events_after_sequence(
                    execution_id=execution.id,
                    after_sequence_no=current_seq,
                    limit=1000,
                ):
                    current_seq = max(current_seq, event.sequence_no)
                    if event.event_type in {"run.finished", "run.failed", "run.interrupted", "run.cancelled", "system.error"}:
                        seen_terminal_event = True
                    yield {
                        "seq": event.sequence_no,
                        "payload": {
                            "event": event.event_type,
                            "data": event.payload,
                        },
                    }

                if seen_terminal_event:
                    break

                terminal_event_type = {
                    ResourceExecutionStatus.SUCCEEDED: "run.finished",
                    ResourceExecutionStatus.INTERRUPTED: "run.interrupted",
                    ResourceExecutionStatus.CANCELLED: "run.cancelled",
                    ResourceExecutionStatus.FAILED: "run.failed",
                }.get(execution.status)
                if terminal_event_type is not None:
                    terminal_event = await service.event_log_service.get_latest_event(
                        execution_id=execution.id,
                        event_type=terminal_event_type,
                    )
                    if terminal_event is not None and terminal_event.sequence_no >= after_seq:
                        seen_terminal_event = True
                        yield {
                            "seq": terminal_event.sequence_no,
                            "payload": {
                                "event": terminal_event.event_type,
                                "data": terminal_event.payload,
                            },
                        }
                        break

                await asyncio.sleep(0.1)

    async def list_runs(
        self,
        instance_uuid: str,
        *,
        limit: int = 20,
    ) -> List[WorkflowRunSummaryRead]:
        service = self.workflow_service
        instance = await service.get_by_uuid(instance_uuid)
        if instance is None:
            raise NotFoundError("Workflow not found.")
        await service._check_execute_perm(instance)

        rows = await service.execution_ledger_service.dao.get_list(
            where={"resource_instance_id": instance.id},
            order=[
                service.execution_ledger_service.dao.model.created_at.desc(),
                service.execution_ledger_service.dao.model.id.desc(),
            ],
            limit=limit,
        )

        summaries: List[WorkflowRunSummaryRead] = []
        for execution in rows:
            latest_checkpoint = await service.runtime_persistence.get_latest_checkpoint(execution_id=execution.id)
            summaries.append(self.build_run_summary(execution, latest_checkpoint))
        return summaries
