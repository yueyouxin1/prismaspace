from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from app.models import ResourceExecutionStatus, User, Workspace
from app.schemas.resource.workflow.workflow_schemas import WorkflowExecutionRequest
from app.services.exceptions import NotFoundError, ServiceException
from app.services.resource.workflow.types.workflow import ExternalContext
from app.engine.workflow import WorkflowGraphDef, WorkflowRuntimePlan, WorkflowRuntimeSnapshot


class WorkflowRunPreparationService:
    """
    负责 workflow run 的准备阶段：协议校验、instance/runtime plan、checkpoint 恢复、execution ledger 初始化。
    """

    def __init__(self, workflow_service):
        self.workflow_service = workflow_service

    async def prepare_run_context(
        self,
        *,
        instance_uuid: str,
        execute_params: WorkflowExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None,
        existing_run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        service = self.workflow_service
        service.resolve_protocol_adapter(execute_params.protocol)

        instance = await service.get_by_uuid(instance_uuid)
        if not instance:
            raise NotFoundError("Workflow not found")
        await service._check_execute_perm(instance)

        workspace = runtime_workspace or instance.resource.workspace
        trace_id = str(uuid.uuid4())
        graph_override = None
        if isinstance(execute_params.meta, dict):
            graph_override = execute_params.meta.get("_workflow_graph_override")
        runtime_plan: Optional[WorkflowRuntimePlan] = None
        restored_snapshot: Optional[WorkflowRuntimeSnapshot] = None
        payload = dict(execute_params.inputs or {})
        resume_payload = None
        requested_thread_id = (execute_params.thread_id or "").strip()
        parent_run_id = (execute_params.parent_run_id or "").strip() or None
        resume_from_run_id = (execute_params.resume_from_run_id or "").strip() or None

        parent_execution = None
        if resume_from_run_id:
            if payload:
                raise ServiceException("Resume execution does not accept new inputs.")
            parent_execution = await service.execution_ledger_service.get_by_run_id(resume_from_run_id)
            if not parent_execution:
                raise NotFoundError("Resume target run not found.")
            if parent_execution.resource_instance_id != instance.id or parent_execution.user_id != actor.id:
                raise ServiceException("Resume target does not belong to this workflow or actor.")
            if parent_execution.status == ResourceExecutionStatus.RUNNING:
                raise ServiceException("Cannot resume a workflow that is still running.")
            resume_payload = await service.run_query_service.resolve_resume_payload(
                parent_execution=parent_execution,
                execute_params=execute_params,
            )

            checkpoint = await service.runtime_persistence.get_latest_checkpoint(execution_id=parent_execution.id)
            if checkpoint is None:
                raise ServiceException("No checkpoint available for resume.")

            runtime_plan = WorkflowRuntimePlan.model_validate(checkpoint.runtime_plan)
            restored_snapshot = service.runtime_persistence.build_resume_snapshot(
                checkpoint=checkpoint,
                runtime_plan=runtime_plan,
            )
            payload = dict(restored_snapshot.payload or {})
            requested_thread_id = parent_execution.thread_id
            parent_run_id = parent_execution.run_id

        elif parent_run_id:
            parent_execution = await service.execution_ledger_service.get_by_run_id(parent_run_id)
            if not parent_execution:
                raise NotFoundError("Parent run not found.")
            if parent_execution.resource_instance_id != instance.id or parent_execution.user_id != actor.id:
                raise ServiceException("Parent run does not belong to this workflow or actor.")
            requested_thread_id = requested_thread_id or parent_execution.thread_id
            if requested_thread_id != parent_execution.thread_id:
                raise ServiceException("Parent run thread mismatch.")

        if runtime_plan is None:
            runtime_plan = service.runtime_compiler.compile(graph_override or instance.graph)

        execution = None
        if existing_run_id:
            execution = await service.execution_ledger_service.get_by_run_id(existing_run_id)
            if execution is None:
                raise NotFoundError("Workflow run not found.")
            if execution.resource_instance_id != instance.id or execution.user_id != actor.id:
                raise ServiceException("Workflow run does not belong to this workflow or actor.")
            requested_thread_id = execution.thread_id
        else:
            thread_id = requested_thread_id or f"workflow-thread-{uuid.uuid4().hex[:16]}"
            execution = await service.execution_ledger_service.create_execution(
                instance=instance,
                actor=actor,
                thread_id=thread_id,
                parent_run_id=parent_run_id,
            )
            await service.db.commit()

        external_context = ExternalContext(
            app_context=service.context,
            workflow_instance=instance,
            runtime_workspace=workspace,
            trace_id=trace_id,
            run_id=execution.run_id,
            thread_id=execution.thread_id,
            resume_payload=resume_payload,
        )
        return {
            "execution": execution,
            "workflow_instance": instance,
            "runtime_plan": runtime_plan,
            "restored_snapshot": restored_snapshot,
            "payload": payload,
            "external_context": external_context,
            "trace_id": trace_id,
        }

    async def build_debug_node_request(
        self,
        *,
        instance_uuid: str,
        node_id: str,
        execute_params: WorkflowExecutionRequest,
    ) -> WorkflowExecutionRequest:
        service = self.workflow_service
        instance = await service.get_by_uuid(instance_uuid)
        if instance is None:
            raise NotFoundError("Workflow not found.")
        await service._check_execute_perm(instance)

        graph_obj = WorkflowGraphDef.model_validate(instance.graph)
        debug_graph = service._build_node_debug_graph(graph_obj, node_id)
        meta = dict(execute_params.meta or {})
        meta["_workflow_graph_override"] = debug_graph
        return WorkflowExecutionRequest(
            protocol=execute_params.protocol,
            inputs=execute_params.inputs,
            meta=meta,
            thread_id=execute_params.thread_id,
            parent_run_id=execute_params.parent_run_id,
            resume_from_run_id=execute_params.resume_from_run_id,
            resume=execute_params.resume,
        )
