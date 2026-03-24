from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.models import ResourceExecutionStatus
from app.schemas.resource.workflow.workflow_schemas import (
    WorkflowEventRead,
    WorkflowExecutionRequest,
    WorkflowRunRead,
    WorkflowRunSummaryRead,
)
from app.services.exceptions import NotFoundError, ServiceException


class WorkflowRunQueryService:
    """
    负责 workflow run 查询、事件查询与 resume 载荷解析。
    """

    def __init__(self, workflow_service):
        self.workflow_service = workflow_service

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
        interrupt = await self.workflow_service.run_persistence_service.get_latest_interrupt(
            execution_id=parent_execution.id,
        )
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

        latest_checkpoint = await service.run_persistence_service.get_latest_checkpoint(execution_id=execution.id)
        node_executions = await service.run_persistence_service.list_node_executions(
            execution_id=execution.id,
        )
        interrupt = await service.run_persistence_service.get_latest_interrupt(
            execution_id=execution.id,
        )

        status_value = execution.status.value if hasattr(execution.status, "value") else str(execution.status)
        can_resume = status_value in {
            ResourceExecutionStatus.FAILED.value,
            ResourceExecutionStatus.CANCELLED.value,
            ResourceExecutionStatus.INTERRUPTED.value,
        } and latest_checkpoint is not None

        return service.run_persistence_service.build_run_detail(
            execution=execution,
            workflow_instance=workflow_instance,
            latest_checkpoint=latest_checkpoint,
            node_executions=node_executions,
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

        return await service.run_persistence_service.list_events(
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

        async for envelope in service.live_event_service.stream_events(run_id, after_seq=after_seq):
            yield envelope

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
            latest_checkpoint = await service.run_persistence_service.get_latest_checkpoint(execution_id=execution.id)
            summaries.append(
                service.run_persistence_service.build_run_summary(
                    execution=execution,
                    latest_checkpoint=latest_checkpoint,
                )
            )
        return summaries
