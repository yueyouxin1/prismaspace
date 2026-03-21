from __future__ import annotations

import uuid
import logging
from typing import Optional

from app.core.trace_manager import TraceManager
from app.models import ResourceExecution, ResourceExecutionStatus, User, Workspace
from app.schemas.protocol import RunAgentInputExt
from app.services.exceptions import ActiveRunExistsError, ConfigurationError, NotFoundError, PermissionDeniedError, ServiceException
from app.services.resource.agent.agent_session_manager import AgentSessionManager
from app.services.resource.agent.processors import ResourceAwareToolExecutor
from app.services.resource.agent.types.agent import AgentRunResult, PreparedAgentRun
from app.utils.async_generator import AsyncGeneratorManager


logger = logging.getLogger(__name__)


class AgentRunPreparationService:
    """
    负责 Agent run 的准备阶段：协议适配、session 绑定、execution ledger 初始化。
    """

    def __init__(self, agent_service):
        self.agent_service = agent_service

    async def prepare_async_run(
        self,
        *,
        instance_uuid: str,
        run_input: RunAgentInputExt,
        actor: User,
        runtime_workspace: Optional[Workspace] = None,
    ) -> PreparedAgentRun:
        service = self.agent_service
        instance = await service.get_runtime_by_uuid(instance_uuid)
        if not instance:
            raise NotFoundError("Agent not found")
        await service._check_execute_perm(instance)

        workspace = await service._resolve_runtime_workspace(
            instance=instance,
            runtime_workspace=runtime_workspace,
        )

        try:
            agent_config = service._parse_agent_config(instance)
        except Exception as exc:
            raise ConfigurationError(f"Agent {instance.uuid} config invalid: {exc}")

        protocol_name = service._resolve_protocol_name(run_input)
        adapter = service.protocol_adapters.get(protocol_name)
        if adapter is None:
            raise ServiceException(f"Unsupported protocol '{protocol_name}'.")

        tool_executor = ResourceAwareToolExecutor(service.context, workspace)
        adapted = adapter.adapt(run_input, tool_registrar=tool_executor)
        if not adapted.input_content and not adapted.custom_history and not adapted.resume_messages:
            raise ServiceException("Agent input content is required.")

        generator_manager = AsyncGeneratorManager()
        trace_id = str(uuid.uuid4())
        requested_thread_id = service._normalize_thread_id(adapted.thread_id)
        if not requested_thread_id:
            raise ServiceException("Agent threadId is required.")
        session_thread_id = service._normalize_uuid(requested_thread_id)
        session_mode = service._resolve_session_mode(run_input)
        requires_session_binding = service._requires_persistent_session_binding(
            run_input,
            requested_thread_id=requested_thread_id,
        )
        parent_run_id = service._normalize_parent_run_id(run_input.parent_run_id)
        resume_interrupt_id = service._normalize_interrupt_id(adapted.resume_interrupt_id)
        if resume_interrupt_id and not parent_run_id:
            parent_run_id = resume_interrupt_id

        parent_execution = None
        parent_checkpoint = None
        resume_checkpoint = None
        turn_id: Optional[str] = None
        execution: Optional[ResourceExecution] = None
        try:
            active_execution = await service.execution_ledger_service.get_latest_active_execution(
                instance=instance,
                actor=actor,
                thread_id=requested_thread_id,
            )
            if active_execution is not None:
                raise ActiveRunExistsError(
                    "An active agent run already exists for this thread. "
                    "Query /active-run and attach /live instead of starting a new run."
                )

            if parent_run_id:
                parent_execution = await service.execution_ledger_service.resolve_parent_execution(
                    parent_run_id=parent_run_id,
                    instance=instance,
                    actor=actor,
                    thread_id=requested_thread_id,
                )
                if parent_execution is None:
                    parent_run_id = None
                else:
                    turn_id = await service.execution_ledger_service.resolve_lineage_root_run_id(
                        execution=parent_execution,
                        instance=instance,
                        actor=actor,
                        thread_id=requested_thread_id,
                    )
                    if not turn_id:
                        parent_execution = None
                        parent_run_id = None
                    else:
                        parent_checkpoint = await service.run_persistence_service.get_checkpoint(
                            execution_id=parent_execution.id
                        )
                        if parent_checkpoint and isinstance(parent_checkpoint.runtime_snapshot, dict):
                            resume_checkpoint = service._restore_runtime_checkpoint(
                                parent_checkpoint.runtime_snapshot
                            )

            if resume_interrupt_id:
                if parent_execution is None:
                    raise ServiceException("resume interruptId is invalid for the current session/thread.")
                if parent_execution.status != ResourceExecutionStatus.INTERRUPTED:
                    raise ServiceException("resume interruptId must reference an interrupted run.")
                if resume_interrupt_id != parent_execution.run_id:
                    raise ServiceException("resume interruptId does not match the parent run.")
                if parent_checkpoint and parent_checkpoint.pending_client_tool_calls:
                    expected_tool_call_ids = {
                        str(item.get("tool_call_id") or item.get("toolCallId"))
                        for item in parent_checkpoint.pending_client_tool_calls
                        if isinstance(item, dict)
                    }
                    provided_tool_call_ids = {item for item in adapted.resume_tool_call_ids if isinstance(item, str)}
                    missing_tool_call_ids = expected_tool_call_ids - provided_tool_call_ids
                    if missing_tool_call_ids:
                        raise ServiceException(
                            "resume interruptId is missing tool results for: " + ", ".join(sorted(missing_tool_call_ids))
                        )
                adapted = service._restore_adapted_from_checkpoint(adapted, parent_checkpoint)

            execution = await service.execution_ledger_service.create_execution(
                instance=instance,
                actor=actor,
                thread_id=requested_thread_id,
                parent_run_id=parent_run_id,
            )
            await service.db.commit()

            turn_id = turn_id or execution.run_id
            canonical_run_input = run_input.model_copy(
                update={
                    "run_id": execution.run_id,
                    "thread_id": requested_thread_id,
                    "parent_run_id": parent_run_id,
                }
            )
            message_ids = service._build_stream_message_ids()
            live_event_buffer = service.live_event_service.create_buffer(execution.run_id)

            session_manager: Optional[AgentSessionManager] = None
            if session_thread_id and session_mode != "stateless":
                candidate = AgentSessionManager(
                    service.context,
                    session_thread_id,
                    execution.run_id,
                    turn_id,
                    trace_id,
                    instance,
                    workspace,
                    actor,
                    create_if_missing=False,
                )
                try:
                    await candidate.initialize()
                    session_manager = candidate
                except (NotFoundError, PermissionDeniedError, ServiceException) as exc:
                    logger.info("Ignoring invalid session-backed thread '%s': %s", session_thread_id, exc)

            if session_manager is None and not adapted.has_custom_history and requires_session_binding:
                error_message = (
                    "A valid threadId (platform session UUID) is required when custom messages history is not provided."
                )
                await service.execution_ledger_service.mark_finished(
                    execution,
                    status=ResourceExecutionStatus.FAILED,
                    error_code="AGENT_SESSION_REQUIRED",
                    error_message=error_message,
                )
                await service.db.commit()
                raise ServiceException(error_message)

            trace_manager = TraceManager(
                db=service.db,
                operation_name="agent.run",
                user_id=actor.id,
                force_trace_id=trace_id,
                target_instance_id=instance.id,
                attributes=None,
            )

            return PreparedAgentRun(
                result=AgentRunResult(
                    generator=generator_manager,
                    config=agent_config,
                    run_id=execution.run_id,
                    turn_id=turn_id,
                    trace_id=trace_id,
                    thread_id=requested_thread_id,
                    detach=live_event_buffer.detach,
                ),
                background_task_kwargs={
                    "agent_config": agent_config,
                    "llm_module_version": instance.llm_module_version,
                    "runtime_workspace": workspace,
                    "trace_manager": trace_manager,
                    "generator_manager": generator_manager,
                    "execution": execution,
                    "turn_id": turn_id,
                    "session_manager": session_manager,
                    "run_input": canonical_run_input,
                    "message_ids": message_ids,
                    "dependencies": None,
                    "adapted": adapted,
                    "tool_executor": tool_executor,
                    "agent_instance": instance,
                    "live_event_buffer": live_event_buffer,
                    "resume_checkpoint": resume_checkpoint,
                },
            )
        except Exception as exc:
            await generator_manager.aclose(force=True)
            await service.db.rollback()
            if execution is not None:
                await service.execution_ledger_service.mark_finished(
                    execution,
                    status=ResourceExecutionStatus.FAILED,
                    error_code="AGENT_RUN_INIT_ERROR",
                    error_message=str(exc),
                )
                await service.db.commit()
            raise
