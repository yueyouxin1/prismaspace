from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from app.core.trace_manager import TraceManager
from app.models import ResourceExecution, ResourceExecutionStatus, User, Workflow, Workspace
from app.schemas.resource.workflow.workflow_schemas import (
    WorkflowExecutionRequest,
    WorkflowExecutionResponse,
    WorkflowExecutionResponseData,
    WorkflowInterruptRead,
    WorkflowRunSummaryRead,
)
from app.services.auditing.types.attributes import WorkflowAttributes
from app.services.exceptions import ServiceException
from app.services.resource.workflow.interceptors import WorkflowTraceInterceptor
from app.services.resource.workflow.live_events import WorkflowLiveEventBuffer
from app.services.resource.workflow.persisting_callbacks import PersistingWorkflowCallbacks
from app.services.resource.workflow.runtime_persistence import WorkflowDurableRuntimeObserver
from app.services.resource.workflow.runtime_registry import WorkflowTaskRegistry
from app.services.resource.workflow.types.workflow import ExternalContext, PreparedWorkflowRun, WorkflowRunResult
from app.engine.workflow import (
    NodeResultData,
    WorkflowInterruptSignal,
    WorkflowRuntimePlan,
    WorkflowRuntimeSnapshot,
)
from app.utils.async_generator import AsyncGeneratorManager


logger = logging.getLogger(__name__)


class WorkflowRunExecutionService:
    """
    负责 workflow run 的执行编排、事件持久化与后台任务协调。
    """

    def __init__(self, workflow_service):
        self.workflow_service = workflow_service

    async def execute(
        self,
        *,
        instance_uuid: str,
        execute_params: WorkflowExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None,
    ) -> WorkflowExecutionResponse:
        service = self.workflow_service
        final_output = None
        trace_id = None
        run_id = None
        thread_id = None
        interrupt_payload = None
        outcome = "success"
        task: Optional[asyncio.Task] = None

        try:
            result = await service.async_execute(
                instance_uuid,
                execute_params,
                actor,
                runtime_workspace,
            )
            task = result.task
            run_id = result.run_id
            thread_id = result.thread_id
            trace_id = result.trace_id
            async for event in result.generator:
                if event.event == "run.started":
                    trace_id = event.data.get("trace_id") or trace_id
                    run_id = event.data.get("run_id") or run_id
                    thread_id = event.data.get("thread_id") or thread_id
                elif event.event == "run.finished":
                    final_output = event.data.get("output")
                    outcome = event.data.get("outcome") or outcome
                elif event.event == "run.failed":
                    error_msg = event.data.get("error") if isinstance(event.data, dict) else str(event.data)
                    raise ServiceException(f"Workflow execution failed: {error_msg}")
                elif event.event == "run.interrupted":
                    interrupt_payload = event.data.get("interrupt") if isinstance(event.data, dict) else None
                    outcome = "interrupt"
                elif event.event == "run.cancelled":
                    outcome = "cancelled"
        except Exception as exc:
            raise ServiceException(f"Workflow failed: {exc}") from exc
        finally:
            if task and not task.done():
                try:
                    await task
                except Exception:
                    pass

        if final_output is None and interrupt_payload is None:
            raise ServiceException("Workflow finished without output.")

        return WorkflowExecutionResponse(
            data=WorkflowExecutionResponseData(
                output=final_output or {},
                trace_id=trace_id or "",
                run_id=run_id,
                thread_id=thread_id,
                outcome=outcome,
                interrupt=WorkflowInterruptRead.model_validate(interrupt_payload) if interrupt_payload else None,
            )
        )

    async def execute_batch(
        self,
        *,
        instance_uuids: list[str],
        execute_params: WorkflowExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None,
    ) -> list[WorkflowExecutionResponse]:
        results = []
        for instance_uuid in instance_uuids:
            results.append(
                await self.execute(
                    instance_uuid=instance_uuid,
                    execute_params=execute_params,
                    actor=actor,
                    runtime_workspace=runtime_workspace,
                )
            )
        return results

    async def enqueue_background_execute(
        self,
        *,
        instance_uuid: str,
        execute_params: WorkflowExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None,
    ) -> WorkflowRunSummaryRead:
        service = self.workflow_service
        prepared = await service.run_preparation_service.prepare_run_context(
            instance_uuid=instance_uuid,
            execute_params=execute_params,
            actor=actor,
            runtime_workspace=runtime_workspace,
        )

        try:
            await service.context.arq_pool.enqueue_job(
                "execute_workflow_run_task",
                run_id=prepared["execution"].run_id,
                instance_uuid=instance_uuid,
                actor_uuid=actor.uuid,
                execute_params=execute_params.model_dump(mode="json", by_alias=True, exclude_none=False),
            )
        except Exception as exc:
            await service.execution_ledger_service.mark_finished(
                prepared["execution"],
                status=ResourceExecutionStatus.FAILED,
                error_code="WORKFLOW_ENQUEUE_ERROR",
                error_message=str(exc),
            )
            await service.db.commit()
            raise ServiceException(f"Failed to enqueue workflow run: {exc}") from exc

        return service.run_query_service.build_run_summary(prepared["execution"], latest_checkpoint=None)

    def _build_callbacks(
        self,
        *,
        prepared: Dict[str, Any],
        generator_manager: AsyncGeneratorManager,
    ) -> tuple[PersistingWorkflowCallbacks, Any]:
        service = self.workflow_service
        live_event_buffer = service.live_event_service.create_buffer(prepared["execution"].run_id)
        callbacks = PersistingWorkflowCallbacks(
            generator_manager=generator_manager,
            trace_id=prepared["trace_id"],
            run_id=prepared["execution"].run_id,
            thread_id=prepared["execution"].thread_id,
            event_sink=live_event_buffer.publish,
        )
        return callbacks, live_event_buffer

    async def prepare_async_run(
        self,
        *,
        instance_uuid: str,
        execute_params: WorkflowExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None,
    ) -> PreparedWorkflowRun:
        service = self.workflow_service
        generator_manager = AsyncGeneratorManager()
        prepared: Optional[Dict[str, Any]] = None

        try:
            prepared = await service.run_preparation_service.prepare_run_context(
                instance_uuid=instance_uuid,
                execute_params=execute_params,
                actor=actor,
                runtime_workspace=runtime_workspace,
            )
            callbacks, live_event_buffer = self._build_callbacks(
                prepared=prepared,
                generator_manager=generator_manager,
            )
            return PreparedWorkflowRun(
                result=WorkflowRunResult(
                    generator=generator_manager,
                    trace_id=prepared["trace_id"],
                    run_id=prepared["execution"].run_id,
                    thread_id=prepared["execution"].thread_id,
                    detach=live_event_buffer.detach,
                ),
                background_task_kwargs={
                    "execution": prepared["execution"],
                    "workflow_instance": prepared["workflow_instance"],
                    "runtime_plan": prepared["runtime_plan"],
                    "restored_snapshot": prepared["restored_snapshot"],
                    "payload": prepared["payload"],
                    "callbacks": callbacks,
                    "generator_manager": generator_manager,
                    "external_context": prepared["external_context"],
                    "trace_id": prepared["trace_id"],
                    "actor": actor,
                    "live_event_buffer": live_event_buffer,
                },
            )
        except Exception:
            await generator_manager.aclose(force=True)
            execution = prepared["execution"] if prepared is not None else None
            if execution is not None:
                await service.execution_ledger_service.mark_finished(
                    execution,
                    status=ResourceExecutionStatus.FAILED,
                    error_code="WORKFLOW_RUN_INIT_ERROR",
                    error_message="Workflow runtime initialization failed.",
                )
                await service.db.commit()
            raise

    async def run_background_task(
        self,
        *,
        execution: ResourceExecution,
        workflow_instance: Workflow,
        runtime_plan: WorkflowRuntimePlan,
        restored_snapshot: Optional[WorkflowRuntimeSnapshot],
        payload: Dict[str, Any],
        callbacks: PersistingWorkflowCallbacks,
        generator_manager: AsyncGeneratorManager,
        external_context: ExternalContext,
        trace_id: str,
        actor: User,
        live_event_buffer: Optional[WorkflowLiveEventBuffer] = None,
    ) -> None:
        service = self.workflow_service
        tracing_interceptor = WorkflowTraceInterceptor(
            db=service.db,
            user_id=actor.id,
            workflow_trace_id=trace_id,
        )
        runtime_observer = WorkflowDurableRuntimeObserver(
            context=service.context,
            execution=execution,
            workflow_instance=workflow_instance,
            runtime_plan=runtime_plan,
            event_callback=callbacks.on_event,
        )

        try:
            await service.execution_ledger_service.mark_running(execution, trace_id=trace_id)
            await service.db.commit()

            async with TraceManager(
                db=service.db,
                operation_name="workflow.run",
                user_id=actor.id,
                force_trace_id=trace_id,
                target_instance_id=workflow_instance.id,
                attributes=WorkflowAttributes(inputs=payload),
            ) as root_span:
                final_output = await service.engine_service.run(
                    workflow_def=runtime_plan,
                    payload=payload,
                    callbacks=callbacks,
                    external_context=external_context,
                    interceptors=[tracing_interceptor],
                    restored_snapshot=restored_snapshot,
                    runtime_observer=runtime_observer,
                )
                root_span.set_output(final_output)

            await service.execution_ledger_service.mark_finished(
                execution,
                status=ResourceExecutionStatus.SUCCEEDED,
            )
            await service.db.commit()
            await service._persist_workflow_run_events(
                execution=execution,
                workflow_instance=workflow_instance,
                callbacks=callbacks,
            )
        except WorkflowInterruptSignal as interrupt_exc:
            interrupt_payload = interrupt_exc.interrupt.model_dump(mode="json")
            await callbacks.on_event(
                "run.interrupted",
                {
                    "interrupt": interrupt_payload,
                    "outcome": "interrupt",
                    "run_id": execution.run_id,
                    "thread_id": execution.thread_id,
                },
            )
            await service.execution_ledger_service.mark_finished(
                execution,
                status=ResourceExecutionStatus.INTERRUPTED,
            )
            await service.db.commit()
            await service._persist_workflow_run_events(
                execution=execution,
                workflow_instance=workflow_instance,
                callbacks=callbacks,
            )
        except asyncio.CancelledError:
            logger.info("Workflow %s execution cancelled.", workflow_instance.uuid)
            await runtime_observer.request_cancel()
            await service.execution_ledger_service.mark_finished(
                execution,
                status=ResourceExecutionStatus.CANCELLED,
                error_code="WORKFLOW_CANCELLED",
                error_message="Operation cancelled.",
            )
            await service.db.commit()
            await callbacks.on_event(
                "run.cancelled",
                {
                    "output": {},
                    "outcome": "cancelled",
                    "run_id": execution.run_id,
                    "thread_id": execution.thread_id,
                },
            )
            await service._persist_workflow_run_events(
                execution=execution,
                workflow_instance=workflow_instance,
                callbacks=callbacks,
            )
            raise
        except Exception as exc:
            logger.error("Workflow execution error: %s", exc, exc_info=True)
            await callbacks.on_event("run.failed", {"error": str(exc)})
            status = (
                ResourceExecutionStatus.CANCELLED
                if await runtime_observer.should_cancel()
                else ResourceExecutionStatus.FAILED
            )
            error_code = "WORKFLOW_CANCELLED" if status == ResourceExecutionStatus.CANCELLED else "WORKFLOW_EXECUTION_ERROR"
            await service.execution_ledger_service.mark_finished(
                execution,
                status=status,
                error_code=error_code,
                error_message=str(exc),
            )
            await service.db.commit()
            await service._persist_workflow_run_events(
                execution=execution,
                workflow_instance=workflow_instance,
                callbacks=callbacks,
            )
        finally:
            WorkflowTaskRegistry.unregister(execution.run_id)
            if live_event_buffer is not None:
                await live_event_buffer.aclose()
            await generator_manager.aclose(force=False)

    async def execute_precreated_run(
        self,
        *,
        run_id: str,
        instance_uuid: str,
        execute_params: WorkflowExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None,
    ) -> None:
        service = self.workflow_service
        prepared = await service.run_preparation_service.prepare_run_context(
            instance_uuid=instance_uuid,
            execute_params=execute_params,
            actor=actor,
            runtime_workspace=runtime_workspace,
            existing_run_id=run_id,
        )
        generator_manager = AsyncGeneratorManager()
        callbacks, live_event_buffer = self._build_callbacks(
            prepared=prepared,
            generator_manager=generator_manager,
        )
        live_event_buffer.detach()
        await self.run_background_task(
            execution=prepared["execution"],
            workflow_instance=prepared["workflow_instance"],
            runtime_plan=prepared["runtime_plan"],
            restored_snapshot=prepared["restored_snapshot"],
            payload=prepared["payload"],
            callbacks=callbacks,
            generator_manager=generator_manager,
            external_context=prepared["external_context"],
            trace_id=prepared["trace_id"],
            actor=actor,
            live_event_buffer=live_event_buffer,
        )
