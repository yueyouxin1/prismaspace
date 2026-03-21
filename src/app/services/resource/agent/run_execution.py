from __future__ import annotations

import asyncio
import logging
from contextlib import nullcontext
from typing import Optional

from app.core.trace_manager import TraceManager
from app.models import ResourceExecution, ResourceExecutionStatus
from app.models.resource.agent import Agent, AgentMessageRole
from app.models.resource.base import ResourceRef
from app.schemas.protocol import RunAgentInputExt
from app.schemas.resource.agent.agent_schemas import AgentConfig
from app.services.common.llm_capability_provider import UsageAccumulator
from app.services.exceptions import ServiceException
from app.services.auditing.types.attributes import AgentAttributes, AgentMeta
from app.services.resource.agent.agent_session_manager import AgentSessionManager
from app.services.resource.agent.live_events import AgentLiveEventBuffer
from app.services.resource.agent.persisting_callbacks import PersistingAgentCallbacks
from app.services.resource.agent.processors import ResourceAwareToolExecutor, ShortContextProcessor
from app.services.resource.agent.protocol_adapter.base import ProtocolAdaptedRun
from app.services.resource.agent.types.agent import AgentStreamMessageIds
from app.engine.agent import AgentInput, AgentResult, AgentRuntimeCheckpoint
from app.engine.model.llm import LLMMessage, LLMRunConfig
from app.utils.async_generator import AsyncGeneratorManager
from app.models import ServiceModuleVersion, Workspace


logger = logging.getLogger(__name__)


class AgentRunExecutionService:
    """
    负责 Agent run 的后台执行协调。
    """

    def __init__(self, agent_service):
        self.agent_service = agent_service

    async def run_background_task(
        self,
        *,
        agent_config: AgentConfig,
        llm_module_version: ServiceModuleVersion,
        runtime_workspace: Workspace,
        trace_manager: TraceManager,
        generator_manager: AsyncGeneratorManager,
        execution: ResourceExecution,
        turn_id: str,
        session_manager: Optional[AgentSessionManager] = None,
        run_input: Optional[RunAgentInputExt] = None,
        message_ids: Optional[AgentStreamMessageIds] = None,
        dependencies: Optional[list[ResourceRef]] = None,
        adapted: Optional[ProtocolAdaptedRun] = None,
        tool_executor: Optional[ResourceAwareToolExecutor] = None,
        agent_instance: Optional[Agent] = None,
        live_event_buffer: Optional[AgentLiveEventBuffer] = None,
        resume_checkpoint: Optional[AgentRuntimeCheckpoint] = None,
    ) -> None:
        service = self.agent_service
        callbacks: Optional[PersistingAgentCallbacks] = None
        usage_accumulator = UsageAccumulator()
        final_result: Optional[AgentResult] = None
        pending_post_commit_dispatch = False
        session = session_manager.session if session_manager and session_manager.session else None

        try:
            async with service.ai_provider:
                callbacks = PersistingAgentCallbacks(
                    generator_manager=generator_manager,
                    session_manager=session_manager,
                    trace_id=trace_manager.force_trace_id,
                    run_id=execution.run_id,
                    turn_id=turn_id,
                    usage_accumulator=usage_accumulator,
                    run_input=run_input,
                    message_ids=message_ids,
                    interrupt_id_builder=service.build_interrupt_id,
                    cancel_checker=lambda: service._should_cancel_run(execution.run_id),
                    event_sink=live_event_buffer.publish if live_event_buffer is not None else None,
                )

                if adapted is None or tool_executor is None or agent_instance is None:
                    raise ServiceException("Agent background task missing runtime prerequisites.")

                lock_ctx = service._session_lock(session.uuid) if session else nullcontext()
                async with lock_ctx:
                    if session:
                        await service.db.refresh(session)
                        preload_turns = ShortContextProcessor.compute_fetch_limit(
                            total_turns=session.turn_count,
                            max_turns=agent_config.io_config.history_turns,
                        )
                        if session.turn_count > 0:
                            await session_manager.preload_recent_messages(max(1, preload_turns))
                        await service._enforce_pending_tool_results(
                            session_manager=session_manager,
                            resume_tool_call_ids=adapted.resume_tool_call_ids,
                        )
                        if adapted.resume_messages:
                            service._buffer_protocol_history_messages(
                                session_manager=session_manager,
                                history=adapted.resume_messages,
                            )

                    if resume_checkpoint is not None:
                        final_messages = [
                            item.model_copy(deep=True) if hasattr(item, "model_copy") else item
                            for item in adapted.resume_messages
                        ]
                        final_tools = [
                            tool.model_copy(deep=True) if hasattr(tool, "model_copy") else tool
                            for tool in (resume_checkpoint.tools or [])
                        ]
                        rendered_system_prompt = ""
                        prompt_variables = {}
                        pipeline_manager = None
                    else:
                        if dependencies is None:
                            dependencies = await service.ref_dao.get_dependencies(agent_instance.id)
                        prompt_variables = await service.agent_memory_var_service.get_runtime_object(
                            agent_instance.version_id,
                            service.context.actor.id,
                            session.uuid if session else None,
                        )
                        rendered_system_prompt = service.prompt_template.render(
                            agent_instance.system_prompt,
                            prompt_variables,
                        )
                        history_messages = [*adapted.custom_history, *adapted.resume_messages]
                        user_message = LLMMessage(role="user", content=adapted.input_content)

                        pipeline_manager = service._build_pipeline_manager(
                            rendered_system_prompt=rendered_system_prompt,
                            user_message=user_message,
                            history_messages=history_messages,
                            tool_executor=tool_executor,
                            agent_config=agent_config,
                            dependencies=dependencies or [],
                            runtime_workspace=runtime_workspace,
                            session_manager=session_manager,
                            prompt_variables=prompt_variables,
                        )
                        final_messages = await pipeline_manager.build_context()
                        final_tools = await pipeline_manager.build_skill()

                    module_context = await service.module_service.get_runtime_context(
                        version_id=llm_module_version.id,
                        actor=service.context.actor,
                        workspace=runtime_workspace,
                    )
                    model_context_window = service._resolve_model_context_window(module_context.version.attributes)

                    run_config = LLMRunConfig(
                        model=module_context.version.name,
                        temperature=agent_config.model_params.temperature,
                        top_p=agent_config.model_params.top_p,
                        presence_penalty=agent_config.model_params.presence_penalty,
                        frequency_penalty=agent_config.model_params.frequency_penalty,
                        max_context_window=model_context_window,
                        max_tokens=agent_config.io_config.max_response_tokens,
                        enable_thinking=agent_config.io_config.enable_deep_thinking,
                        thinking_budget=agent_config.io_config.max_thinking_tokens,
                        tools=final_tools,
                        stream=True,
                    )

                    if session and resume_checkpoint is None:
                        session_manager.buffer_message(
                            role=AgentMessageRole.USER,
                            message_uuid=message_ids.user_message_id if message_ids else None,
                            text_content=user_message.content if isinstance(user_message.content, str) else None,
                            content_parts=user_message.content if isinstance(user_message.content, list) else None,
                        )

                    await service.execution_ledger_service.mark_running(execution, trace_id=trace_manager.force_trace_id)

                    async with trace_manager as root_span:
                        try:
                            agent_input = AgentInput(messages=final_messages)
                            root_span.attributes = AgentAttributes(
                                meta=AgentMeta(config=run_config),
                                inputs=agent_input,
                            )
                            result = await service.ai_provider.execute_agent_with_billing(
                                runtime_workspace=runtime_workspace,
                                module_context=module_context,
                                agent_input=agent_input,
                                run_config=run_config,
                                tool_executor=pipeline_manager.tool_executor if pipeline_manager is not None else tool_executor,
                                callbacks=callbacks,
                                usage_accumulator=usage_accumulator,
                                resume_checkpoint=resume_checkpoint,
                            )
                            final_result = result
                            root_span.set_output(result)
                        except Exception:
                            if callbacks.final_result:
                                root_span.set_output(callbacks.final_result)
                            raise
                        finally:
                            if session:
                                await session_manager.commit(agent_config.deep_memory)
                                pending_post_commit_dispatch = True

            outcome = (callbacks.final_result.outcome if callbacks and callbacks.final_result else None) or getattr(final_result, "outcome", None)
            status = ResourceExecutionStatus.SUCCEEDED
            if outcome == "interrupted":
                status = ResourceExecutionStatus.INTERRUPTED
            elif outcome == "cancelled":
                status = ResourceExecutionStatus.CANCELLED

            await service.execution_ledger_service.mark_finished(execution, status=status)
            await service.db.commit()
            if pending_post_commit_dispatch and session_manager:
                await session_manager.dispatch_post_commit_jobs()
            if callbacks:
                try:
                    await callbacks.emit_prepared_terminal_event()
                except Exception as exc:
                    logger.error("Failed to emit terminal event for run %s: %s", execution.run_id, exc, exc_info=True)

            if status == ResourceExecutionStatus.INTERRUPTED:
                checkpoint_snapshot = callbacks.get_runtime_checkpoint_snapshot() if callbacks else {}
                await service._upsert_run_checkpoint(
                    execution=execution,
                    agent_instance=agent_instance,
                    session=session,
                    thread_id=getattr(execution, "thread_id", ""),
                    turn_id=turn_id,
                    checkpoint_kind="interrupted",
                    run_input=run_input,
                    adapted=adapted,
                    runtime_snapshot=checkpoint_snapshot,
                    pending_client_tool_calls=checkpoint_snapshot.get("pending_client_tool_calls", []),
                )
            else:
                execution_id = getattr(execution, "id", None)
                if execution_id is not None:
                    await service._delete_run_checkpoint(execution_id)
            await service._persist_agent_run_artifacts(
                execution=execution,
                agent_instance=agent_instance,
                session=session,
                turn_id=turn_id,
                callbacks=callbacks,
            )
            await service._clear_cancel_run(execution.run_id)

        except asyncio.CancelledError:
            logger.info("Agent task cancelled. TraceID: %s", trace_manager.force_trace_id)
            if session_manager:
                session_manager.clear_post_commit_jobs()
            await service.db.rollback()
            await service.execution_ledger_service.mark_finished(
                execution,
                status=ResourceExecutionStatus.CANCELLED,
                error_code="AGENT_CANCELLED",
                error_message="Operation cancelled.",
            )
            await service.db.commit()
            execution_id = getattr(execution, "id", None)
            if execution_id is not None:
                await service._delete_run_checkpoint(execution_id)
            if callbacks and callbacks.pending_terminal_event:
                try:
                    await callbacks.emit_prepared_terminal_event()
                except Exception as exc:
                    logger.error("Failed to emit cancel terminal event for run %s: %s", execution.run_id, exc, exc_info=True)
            await service._persist_agent_run_artifacts(
                execution=execution,
                agent_instance=agent_instance,
                session=session,
                turn_id=turn_id,
                callbacks=callbacks,
            )
            await service._clear_cancel_run(execution.run_id)
            raise
        except Exception as exc:
            logger.error("Agent task error: %s", exc, exc_info=True)
            if session_manager:
                session_manager.clear_post_commit_jobs()
            await service.db.rollback()
            if callbacks and not callbacks.has_terminal_event:
                await callbacks.on_agent_error(exc)
            await service.execution_ledger_service.mark_finished(
                execution,
                status=ResourceExecutionStatus.FAILED,
                error_code="AGENT_EXECUTION_ERROR",
                error_message=str(exc),
            )
            await service.db.commit()
            execution_id = getattr(execution, "id", None)
            if execution_id is not None:
                await service._delete_run_checkpoint(execution_id)
            await service._persist_agent_run_artifacts(
                execution=execution,
                agent_instance=agent_instance,
                session=session,
                turn_id=turn_id,
                callbacks=callbacks,
            )
            await service._clear_cancel_run(execution.run_id)
        finally:
            if live_event_buffer is not None:
                await live_event_buffer.aclose()
            await generator_manager.aclose(force=False)
