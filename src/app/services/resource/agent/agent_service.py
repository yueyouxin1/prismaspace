# src/app/services/resource/agent/agent_service.py

import json
import logging
import uuid
import asyncio
import time
from typing import Dict, Any, List, Callable, Optional, AsyncGenerator, Union
from decimal import Decimal
from contextlib import asynccontextmanager, nullcontext
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from app.core.context import AppContext
from app.core.trace_manager import TraceManager
from app.utils.async_generator import AsyncGeneratorManager
from app.models import User, Team, Workspace, Resource, ResourceInstance, ResourceRef, VersionStatus, ChatMessage, ServiceModuleVersion
from app.models.resource.agent import Agent
from app.models.interaction.chat import ChatSession, MessageRole
from app.dao.resource.agent.agent_dao import AgentDao
from app.dao.module.service_module_dao import ServiceModuleVersionDao
from app.dao.resource.resource_ref_dao import ResourceRefDao
from app.dao.product.feature_dao import FeatureDao

# Schemas
from app.schemas.resource.agent.agent_schemas import (
    AgentUpdate, AgentRead, AgentConfig, GenerationDiversity, AgentRAGConfig, ModelParams,
    InputOutputConfig, DeepMemoryConfig
)
from app.schemas.protocol import (
    AgUiInterrupt,
    AgUiInterruptPayload,
    AgUiInterruptToolCall,
    RunFinishedEventExt,
    RunAgentInputExt,
    RunEventsResponse,
)
from app.schemas.resource.knowledge.knowledge_schemas import KnowledgeBaseExecutionRequest, KnowledgeBaseExecutionParams, SearchResultChunk
from app.services.auditing.types.attributes import (
    AgentAttributes, AgentMeta, LLMMeta, LLMAttributes
)

# Services & Logic
from app.services.common.llm_capability_provider import AICapabilityProvider, UsageAccumulator
from app.services.resource.base.base_impl_service import register_service, ResourceImplementationService, ValidationResult, DependencyInfo
from app.services.resource.base.base_resource_service import BaseResourceService
from app.services.module.service_module_service import ServiceModuleService
from app.services.billing.context import BillingContext
from app.services.resource.agent.agent_session_manager import AgentSessionManager
from app.services.resource.agent.memory.agent_memory_var_service import AgentMemoryVarService
from app.services.resource.agent.prompt_template import PromptTemplate
from app.services.resource.agent.memory.deep.long_term_context_service import LongTermContextService
from app.services.resource.agent.memory.deep.context_summary_service import ContextSummaryService
from app.services.resource.agent.pipeline_manager import AgentPipelineManager
from app.services.resource.agent.processors import ResourceAwareToolExecutor
from app.services.resource.agent.ag_ui_normalizer import AgUiNormalizer
from app.services.resource.agent.ag_ui_processor import AgUiProcessor
from app.services.exceptions import ServiceException, NotFoundError, ConfigurationError
from app.services.product.types.feature import FeatureRole
from app.services.resource.agent.types.agent import AgentRunResult

# Engine
from app.engine.agent import (
    AgentEngineService, AgentInput, AgentStep, AgentResult, AgentClientToolCall, AgentEngineCallbacks, BaseToolExecutor
)
from app.engine.model.llm import (
    LLMEngineService, LLMProviderConfig, LLMRunConfig, LLMMessage, LLMTool, LLMToolCall, LLMToolCallChunk, LLMUsage, LLMEngineCallbacks
)
from app.engine.utils.tokenizer.manager import tokenizer_manager
from ag_ui.core import (
    CustomEvent,
    EventType,
    MessagesSnapshotEvent,
    ReasoningEndEvent,
    ReasoningMessageContentEvent,
    ReasoningMessageEndEvent,
    ReasoningMessageStartEvent,
    ReasoningStartEvent,
    RunErrorEvent,
    RunStartedEvent,
    StateDeltaEvent,
    StateSnapshotEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)

logger = logging.getLogger(__name__)


class PersistingAgentCallbacks(AgentEngineCallbacks):
    """
    [Production] 负责事件推送、状态追踪和计费数据收集。
    """
    def __init__(
        self, 
        usage_accumulator: UsageAccumulator,
        generator_manager: AsyncGeneratorManager, 
        session_manager: Optional[AgentSessionManager] = None, 
        trace_id: Optional[str] = None,
        run_input: Optional[RunAgentInputExt] = None,
    ):
        self.trace_id = trace_id
        self.session_manager = session_manager
        self.generator_manager = generator_manager
        self.usage_accumulator = usage_accumulator
        self.run_input = run_input
        self.input_payload = (
            run_input.model_dump(mode="json", by_alias=True, exclude_none=True)
            if run_input
            else {}
        )
        self.messages_snapshot = self.input_payload.get("messages", []) if isinstance(self.input_payload, dict) else []
        self.final_result: Optional[AgentResult] = None
        self.has_terminal_event = False
        self.assistant_message_id = (
            f"assistant-{run_input.run_id}" if run_input else f"assistant-{self.trace_id}"
        )
        self.reasoning_message_id = (
            f"reasoning-{run_input.run_id}" if run_input else f"reasoning-{self.trace_id}"
        )
        self.text_started = False
        self.reasoning_started = False
        self.tool_call_stream_states: Dict[str, Dict[str, Any]] = {}
        self.tool_call_index_states: Dict[int, Dict[str, Any]] = {}

    def _base_meta(self) -> Dict[str, Any]:
        session_uuid = self.session_manager.session.uuid if self.session_manager and self.session_manager.session else None
        return {
            "traceId": self.trace_id,
            "sessionUuid": session_uuid,
            "turnId": self.trace_id,
            "ts": int(time.time() * 1000),
        }

    async def _emit(self, event: Any):
        await self.generator_manager.put(event)

    async def _emit_custom(self, name: str, value: Any):
        await self._emit(
            CustomEvent(
                type=EventType.CUSTOM,
                name=name,
                value=value,
            )
        )

    @staticmethod
    def _normalize_tool_args(tool_args: Any) -> str:
        if isinstance(tool_args, str):
            return tool_args
        if tool_args is None:
            return ""
        return json.dumps(tool_args, ensure_ascii=False)

    async def _emit_tool_call_start(self, tool_call_id: str, tool_name: str):
        await self._emit(
            ToolCallStartEvent(
                type=EventType.TOOL_CALL_START,
                tool_call_id=tool_call_id,
                tool_call_name=tool_name,
                parent_message_id=self.assistant_message_id,
            )
        )

    async def _emit_tool_call_args(self, tool_call_id: str, delta: str):
        await self._emit(
            ToolCallArgsEvent(
                type=EventType.TOOL_CALL_ARGS,
                tool_call_id=tool_call_id,
                delta=delta,
            )
        )

    async def _emit_tool_call_end(self, tool_call_id: str):
        await self._emit(
            ToolCallEndEvent(
                type=EventType.TOOL_CALL_END,
                tool_call_id=tool_call_id,
            )
        )

    async def _close_open_messages(self):
        if self.text_started:
            self.text_started = False
            await self._emit(
                TextMessageEndEvent(
                    type=EventType.TEXT_MESSAGE_END,
                    message_id=self.assistant_message_id,
                )
            )
        if self.reasoning_started:
            self.reasoning_started = False
            await self._emit(
                ReasoningMessageEndEvent(
                    type=EventType.REASONING_MESSAGE_END,
                    message_id=self.reasoning_message_id,
                )
            )
            await self._emit(
                ReasoningEndEvent(
                    type=EventType.REASONING_END,
                    message_id=self.reasoning_message_id,
                )
            )

    async def on_agent_start(self):
        if not self.run_input:
            return
        await self._emit(
            RunStartedEvent(
                type=EventType.RUN_STARTED,
                thread_id=self.run_input.thread_id,
                run_id=self.run_input.run_id,
                parent_run_id=self.run_input.parent_run_id,
                input=self.input_payload,
            )
        )
        await self._emit(
            MessagesSnapshotEvent(
                type=EventType.MESSAGES_SNAPSHOT,
                messages=self.messages_snapshot,
            )
        )
        await self._emit(
            StateSnapshotEvent(
                type=EventType.STATE_SNAPSHOT,
                snapshot=self.run_input.state,
            )
        )
        await self._emit(
            StateDeltaEvent(
                type=EventType.STATE_DELTA,
                delta=[{"op": "add", "path": "/runStatus", "value": "running"}],
            )
        )
        await self._emit_custom("ps.meta.trace", self._base_meta())

    async def on_tool_calls_generated(self, tool_calls: List[LLMToolCall]):
        tool_calls_data = [tc.model_dump(mode="json") for tc in tool_calls]
        for tool_call in tool_calls_data:
            function = tool_call.get("function", {}) or {}
            tool_call_id = tool_call.get("id", "")
            tool_name = function.get("name", "")
            tool_args = self._normalize_tool_args(function.get("arguments", "{}"))

            state = self.tool_call_stream_states.get(tool_call_id)
            if not state:
                state = {
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "started": False,
                    "args_emitted": False,
                    "ended": False,
                    "pending_args": [],
                }
                self.tool_call_stream_states[tool_call_id] = state

            if tool_name and not state.get("tool_name"):
                state["tool_name"] = tool_name

            if not state.get("started"):
                await self._emit_tool_call_start(
                    tool_call_id=tool_call_id,
                    tool_name=state.get("tool_name", "") or "",
                )
                state["started"] = True

            if not state.get("args_emitted") and tool_args:
                await self._emit_tool_call_args(tool_call_id=tool_call_id, delta=tool_args)
                state["args_emitted"] = True

            if not state.get("ended"):
                await self._emit_tool_call_end(tool_call_id=tool_call_id)
                state["ended"] = True

        # 一轮工具调用收口后清理状态，避免跨轮污染。
        self.tool_call_stream_states.clear()
        self.tool_call_index_states.clear()

        if self.session_manager:
            self.session_manager.buffer_message(role=MessageRole.ASSISTANT, tool_calls=tool_calls_data)

    async def on_tool_call_chunk_generated(self, chunk: LLMToolCallChunk):
        index_state = self.tool_call_index_states.setdefault(
            chunk.index,
            {
                "tool_call_id": None,
                "tool_name": "",
                "started": False,
                "args_emitted": False,
                "ended": False,
                "pending_args": [],
            },
        )

        if chunk.tool_name:
            index_state["tool_name"] = chunk.tool_name
        if chunk.tool_call_id:
            index_state["tool_call_id"] = chunk.tool_call_id
            self.tool_call_stream_states[chunk.tool_call_id] = index_state

        tool_call_id = index_state.get("tool_call_id")
        if not tool_call_id:
            if chunk.arguments_delta:
                index_state["pending_args"].append(chunk.arguments_delta)
            return

        if not index_state.get("started"):
            await self._emit_tool_call_start(
                tool_call_id=tool_call_id,
                tool_name=index_state.get("tool_name", "") or "",
            )
            index_state["started"] = True

        pending_args = index_state.get("pending_args") or []
        for pending_delta in pending_args:
            await self._emit_tool_call_args(tool_call_id=tool_call_id, delta=pending_delta)
            index_state["args_emitted"] = True
        index_state["pending_args"] = []

        if chunk.arguments_delta:
            await self._emit_tool_call_args(tool_call_id=tool_call_id, delta=chunk.arguments_delta)
            index_state["args_emitted"] = True

    async def on_agent_step(self, step: AgentStep):
        if step.thought and self.session_manager:
            self.session_manager.buffer_message(
                role=MessageRole.REASONING,
                text_content=step.thought,
                reasoning_content=step.thought,
                activity_type="tool_call_thought",
            )

        output_text = (
            step.observation
            if isinstance(step.observation, str)
            else json.dumps(step.observation, ensure_ascii=False)
        )
        await self._emit(
            ToolCallResultEvent(
                type=EventType.TOOL_CALL_RESULT,
                message_id=self.assistant_message_id,
                tool_call_id=step.action.id,
                content=output_text,
                role="tool",
            )
        )
        if self.session_manager:
            self.session_manager.buffer_message(
                role=MessageRole.TOOL,
                tool_call_id=step.action.id,
                text_content=output_text,
            )

    async def on_final_chunk_generated(self, chunk: str):
        if not chunk:
            return
        if not self.text_started:
            self.text_started = True
            await self._emit(
                TextMessageStartEvent(
                    type=EventType.TEXT_MESSAGE_START,
                    message_id=self.assistant_message_id,
                    role="assistant",
                )
            )
        await self._emit(
            TextMessageContentEvent(
                type=EventType.TEXT_MESSAGE_CONTENT,
                message_id=self.assistant_message_id,
                delta=chunk,
            )
        )

    async def on_reasoning_chunk_generated(self, chunk: str):
        if not chunk:
            return
        if not self.reasoning_started:
            self.reasoning_started = True
            await self._emit(
                ReasoningStartEvent(
                    type=EventType.REASONING_START,
                    message_id=self.reasoning_message_id,
                )
            )
            await self._emit(
                ReasoningMessageStartEvent(
                    type=EventType.REASONING_MESSAGE_START,
                    message_id=self.reasoning_message_id,
                    role="assistant",
                )
            )
        await self._emit(
            ReasoningMessageContentEvent(
                type=EventType.REASONING_MESSAGE_CONTENT,
                message_id=self.reasoning_message_id,
                delta=chunk,
            )
        )

    @staticmethod
    def _to_interrupt_payload(client_tool_calls: List[AgentClientToolCall]) -> AgUiInterruptPayload:
        tool_calls: List[AgUiInterruptToolCall] = []
        for call in client_tool_calls:
            tool_calls.append(
                AgUiInterruptToolCall(
                    toolCallId=call.tool_call_id,
                    name=call.name,
                    arguments=call.arguments,
                )
            )
        return AgUiInterruptPayload(toolCalls=tool_calls)

    async def _persist_assistant(self, result: AgentResult):
        if not self.session_manager:
            return
        text_content = result.message.content if isinstance(result.message.content, str) else None
        content_parts = result.message.content if isinstance(result.message.content, list) else None
        reasoning_content = result.reasoning_content
        if not text_content and not content_parts and not reasoning_content:
            return
        self.session_manager.buffer_message(
            role=MessageRole.ASSISTANT,
            text_content=text_content,
            content_parts=content_parts,
            reasoning_content=reasoning_content,
        )

    async def on_agent_finish(self, result: AgentResult):
        self.final_result = result
        if self.has_terminal_event:
            return
        self.has_terminal_event = True
        await self._close_open_messages()
        await self._persist_assistant(result)
        if self.run_input:
            await self._emit(
                RunFinishedEventExt(
                    type=EventType.RUN_FINISHED,
                    thread_id=self.run_input.thread_id,
                    run_id=self.run_input.run_id,
                    outcome="success",
                    result=result.model_dump(mode="json"),
                )
            )
            await self._emit(
                StateDeltaEvent(
                    type=EventType.STATE_DELTA,
                    delta=[{"op": "replace", "path": "/runStatus", "value": "completed"}],
                )
            )

    async def on_agent_interrupt(self, result: AgentResult):
        self.final_result = result
        if self.has_terminal_event:
            return
        self.has_terminal_event = True
        await self._close_open_messages()
        await self._persist_assistant(result)
        if self.run_input:
            await self._emit(
                RunFinishedEventExt(
                    type=EventType.RUN_FINISHED,
                    thread_id=self.run_input.thread_id,
                    run_id=self.run_input.run_id,
                    outcome="interrupt",
                    interrupt=AgUiInterrupt(
                        id=f"interrupt-{self.trace_id}",
                        reason="tool_result_required",
                        payload=self._to_interrupt_payload(result.client_tool_calls or []),
                    ),
                    result=result.model_dump(mode="json"),
                )
            )
            await self._emit(
                StateDeltaEvent(
                    type=EventType.STATE_DELTA,
                    delta=[{"op": "replace", "path": "/runStatus", "value": "interrupted"}],
                )
            )

    async def on_agent_cancel(self, result: AgentResult) -> None:
        self.final_result = result
        if self.has_terminal_event:
            return
        self.has_terminal_event = True
        await self._close_open_messages()
        await self._persist_assistant(result)
        if self.run_input:
            await self._emit(
                RunFinishedEventExt(
                    type=EventType.RUN_FINISHED,
                    thread_id=self.run_input.thread_id,
                    run_id=self.run_input.run_id,
                    outcome="cancelled",
                    result=result.model_dump(mode="json"),
                )
            )
            await self._emit(
                StateDeltaEvent(
                    type=EventType.STATE_DELTA,
                    delta=[{"op": "replace", "path": "/runStatus", "value": "cancelled"}],
                )
            )

    async def on_agent_error(self, error: Exception):
        if self.has_terminal_event:
            return
        self.has_terminal_event = True
        await self._close_open_messages()
        if self.run_input:
            await self._emit(
                RunErrorEvent(
                    type=EventType.RUN_ERROR,
                    thread_id=self.run_input.thread_id,
                    run_id=self.run_input.run_id,
                    code="AGENT_EXECUTION_ERROR",
                    message=str(error),
                    retriable=False,
                )
            )
            await self._emit(
                StateDeltaEvent(
                    type=EventType.STATE_DELTA,
                    delta=[{"op": "replace", "path": "/runStatus", "value": "error"}],
                )
            )

    async def on_usage(self, usage: LLMUsage):
        if self.usage_accumulator:
            self.usage_accumulator.add(usage)
        await self._emit_custom(
            "ps.meta.usage",
            {
                **self._base_meta(),
                **usage.model_dump(mode="json"),
            },
        )

@register_service
class AgentService(ResourceImplementationService):
    name: str = "agent"

    def __init__(self, context: AppContext):
        super().__init__(context)
        self.dao = AgentDao(context.db)
        self.ref_dao = ResourceRefDao(context.db)
        self.feature_dao = FeatureDao(context.db)
        self.redis = context.redis_service
        self.module_service = ServiceModuleService(context)
        self.ai_provider = AICapabilityProvider(context)
        self.agent_memory_var_service = AgentMemoryVarService(context)
        self.long_term_service = LongTermContextService(context)
        self.prompt_template = PromptTemplate()
        self.ag_ui_normalizer = AgUiNormalizer()
        self.ag_ui_processor = AgUiProcessor(self.ag_ui_normalizer)
        self.resource_resolver = BaseResourceService(context)

    # ==========================================================================
    # Execution Logic (The Core)
    # ==========================================================================

    @staticmethod
    def _event_to_payload(event: Any) -> Dict[str, Any]:
        if hasattr(event, "model_dump"):
            return event.model_dump(mode="json", by_alias=True, exclude_none=True)
        if isinstance(event, dict):
            return event
        return {"type": "CUSTOM", "name": "ps.meta.unknown_event", "value": str(event)}

    async def execute(
        self, 
        instance_uuid: str, 
        run_input: RunAgentInputExt,
        actor: User,
        runtime_workspace: Optional[Workspace] = None
    ) -> RunEventsResponse:
        stream_result = await self.async_execute(
            instance_uuid=instance_uuid,
            run_input=run_input,
            actor=actor,
            runtime_workspace=runtime_workspace,
        )

        events: List[Dict[str, Any]] = []
        try:
            async for event in stream_result.generator:
                events.append(self._event_to_payload(event))
        except Exception as exc:
            logger.error("Critical non-stream execution failure: %s", exc, exc_info=True)
            events.append(
                {
                    "type": "RUN_ERROR",
                    "threadId": run_input.thread_id,
                    "runId": run_input.run_id,
                    "code": "AGENT_RUNTIME_ERROR",
                    "message": str(exc),
                    "retriable": False,
                }
            )

        return RunEventsResponse(
            threadId=run_input.thread_id,
            runId=run_input.run_id,
            events=events,
        )

    async def execute_batch(
        self,
        instance_uuids: List[str],
        run_input: RunAgentInputExt,
        actor: User,
        runtime_workspace: Optional[Workspace] = None
    ) -> List[RunEventsResponse]:
        """
        Agent 暂不支持真正的并行 Batch（每个都是独立的有状态循环）。
        简单实现为循环调用。
        """
        results = []
        for uuid in instance_uuids:
            res = await self.execute(uuid, run_input, actor, runtime_workspace)
            results.append(res)
        
        return results

    async def async_execute(
        self, 
        instance_uuid: str, 
        run_input: RunAgentInputExt,
        actor: User,
        runtime_workspace: Optional[Workspace] = None
    ) -> AgentRunResult:
        # 1. Load Instance & Workspace
        instance = await self.get_by_uuid(instance_uuid)
        if not instance: raise NotFoundError("Agent not found")
        await self._check_execute_perm(instance)

        workspace = runtime_workspace or instance.resource.workspace

        # 2. Parse Configuration
        try:
            agent_config = AgentConfig(**instance.agent_config)
        except Exception as e:
            raise ConfigurationError(f"Agent {instance.uuid} config invalid: {e}")

        # 3. AG-UI input normalization
        processed = self.ag_ui_processor.agui_to_agent_runtime(run_input)
        if not processed.input_content and not processed.history:
            raise ServiceException("Agent input content is required.")

        generator_manager = AsyncGeneratorManager()
        dependencies = await self.ref_dao.get_dependencies(instance.id)

        # 4. Session Manager
        trace_id = str(uuid.uuid4())
        session_manager = AgentSessionManager(
            self.context,
            processed.session_uuid,
            trace_id,
            instance,
            workspace,
            actor,
            create_if_missing=True,
        )
        await session_manager.initialize()
        session = session_manager.session

        # 5. Prompt Rendering
        prompt_variables = await self.agent_memory_var_service.get_runtime_object(
            instance.version_id, actor.id, session.uuid if session else None
        )
        rendered_system_prompt = self.prompt_template.render(instance.system_prompt, prompt_variables)

        # 7. Pipeline Manager
        system_message = LLMMessage(role="system", content=rendered_system_prompt)
        history_messages = processed.history if not session else None
        user_message = LLMMessage(role="user", content=processed.input_content)
        tool_executor = ResourceAwareToolExecutor(self.context, workspace)

        # AG-UI client-side tools are first-class tools in the run config,
        # but their execution happens on the client and is resumed via interrupt flow.
        for tool_def in processed.llm_tools:
            try:
                tool_executor.register_client_tool(tool_def)
            except Exception as e:
                logger.warning(f"Invalid AG-UI tool definition ignored: {e}")

        pipeline_manager = AgentPipelineManager(
            system_message=system_message,
            user_message=user_message,
            history=history_messages,
            tool_executor=tool_executor
        ).add_standard_processors(
            app_context=self.context, 
            agent_config=agent_config,
            dependencies=dependencies,
            runtime_workspace=workspace,
            session_manager=session_manager,
            prompt_variables=prompt_variables
        )

        trace_manager = TraceManager(
            db=self.db,
            operation_name="agent.run",
            user_id=actor.id,
            force_trace_id=trace_id,
            target_instance_id=instance.id,
            # 运行时自动补全attributes
            attributes=None
        )

        # 10. Launch Task
        run_task = asyncio.create_task(self._run_agent_background_task(
            agent_config=agent_config,
            llm_module_version=instance.llm_module_version,
            pipeline_manager=pipeline_manager,
            runtime_workspace=workspace,
            trace_manager=trace_manager,
            generator_manager=generator_manager,
            session_manager=session_manager,
            run_input=run_input,
        ))

        return AgentRunResult(
            generator=generator_manager,
            config=agent_config,
            trace_id=trace_id,
            cancel=lambda: (not run_task.done()) and run_task.cancel(),
        )

    @asynccontextmanager
    async def _session_lock(self, session_uuid: str):
        """
        [Concurrency Guard] 分布式锁，防止同一个会话并发写入。
        """
        lock_key = f"lock:session:{session_uuid}"
        # 使用 RedisService 提供的 client 获取锁
        lock = self.redis.client.lock(lock_key, timeout=60, blocking_timeout=5)
        
        acquired = await lock.acquire()
        if not acquired:
            raise ServiceException("Session is busy. Please wait for the previous response to finish.")
        try:
            yield
        finally:
            await lock.release()

    # ==========================================================================
    # Core Runtime Logic (Decoupled)
    # ==========================================================================

    async def _run_agent_background_task(
        self,
        agent_config: AgentConfig,
        llm_module_version: ServiceModuleVersion,
        pipeline_manager: AgentPipelineManager,
        runtime_workspace: Workspace,
        trace_manager: TraceManager,
        generator_manager: AsyncGeneratorManager,
        session_manager: Optional[AgentSessionManager] = None,
        run_input: Optional[RunAgentInputExt] = None,
    ):

        callbacks: Optional[PersistingAgentCallbacks] = None
        try:
            usage_accumulator = UsageAccumulator()

            callbacks = PersistingAgentCallbacks(
                generator_manager=generator_manager,
                session_manager=session_manager,
                trace_id=trace_manager.force_trace_id,
                usage_accumulator=usage_accumulator,
                run_input=run_input,
            )

            # A. Pipeline Execution
            final_messages = await pipeline_manager.build_context()
            final_tools = await pipeline_manager.build_skill()

            # B. Prepare Contexts
            module_context = await self.module_service.get_runtime_context(
                version_id=llm_module_version.id,
                actor=self.context.actor,
                workspace=runtime_workspace
            )

            run_config = LLMRunConfig(
                model=module_context.version.name,
                temperature=agent_config.model_params.temperature,
                top_p=agent_config.model_params.top_p,
                presence_penalty=agent_config.model_params.presence_penalty,
                frequency_penalty=agent_config.model_params.frequency_penalty,
                # max_context_window=llm_attributes.get("context_window", 4096),
                max_tokens=agent_config.io_config.max_response_tokens,
                enable_thinking=agent_config.io_config.enable_deep_thinking,
                thinking_budget=agent_config.io_config.max_thinking_tokens,
                tools=final_tools, 
                stream=True
            )

            # C. Session Locking & Trace Context
            session = session_manager.session if session_manager and session_manager.session else None
            lock_ctx = self._session_lock(session.uuid) if session else nullcontext()
            if session:
                # Buffer user input
                user_message = pipeline_manager.user_message
                session_manager.buffer_message(
                    role=MessageRole.USER,
                    text_content=user_message.content if isinstance(user_message.content, str) else None,
                    content_parts=user_message.content if isinstance(user_message.content, list) else None,
                )

            async with lock_ctx:
                # [TRACE SCOPE START]
                async with trace_manager as root_span:
                    try:
                        agent_input=AgentInput(messages=final_messages)
                        root_span.attributes = AgentAttributes(
                            # 不再需要记录用量，因为result包含了
                            meta=AgentMeta(config=run_config),
                            inputs=agent_input
                        )
                        result = await self.ai_provider.execute_agent_with_billing(
                            runtime_workspace=runtime_workspace,
                            module_context=module_context,
                            agent_input=agent_input,
                            run_config=run_config,
                            tool_executor=pipeline_manager.tool_executor,
                            callbacks=callbacks,
                            usage_accumulator=usage_accumulator
                        )
                        # Happy Path: Update Trace
                        root_span.set_output(result)
                    except Exception as e:
                        # Error Path: Try to recover partial result for Trace
                        if callbacks.final_result:
                            # 如果是 on_agent_cancel 产生的 result，包含部分内容
                            root_span.set_output(callbacks.final_result)
                        raise # Re-raise to trigger outer catch block
                    finally:
                        # Session Commit (Inside Trace Scope for correct timing, but protected)
                        if session:
                            try:
                                await session_manager.commit(agent_config.deep_memory)
                            except Exception as e:
                                logger.error(f"Failed to commit session: {e}", exc_info=True)
                # [TRACE SCOPE END] - Span is written to DB here

        except asyncio.CancelledError:
            logger.info(f"Agent task cancelled. TraceID: {trace_manager.force_trace_id}")
            raise 
        except Exception as e:
            logger.error(f"Agent task error: {e}", exc_info=True)
            if callbacks and not callbacks.has_terminal_event:
                await callbacks.on_agent_error(e)
        finally:
            await generator_manager.aclose(force=False)

    # --- CRUD Implementation ---

    async def get_by_uuid(self, instance_uuid: str) -> Optional[Agent]:
        return await self.dao.get_by_uuid(instance_uuid)

    async def create_instance(self, resource: Resource, actor: User) -> Agent:
        # 1. 获取默认 LLM 模型
        default_llm = await self.module_service.smv_dao.get_default_version_by_type("llm")
        if not default_llm:
            raise ConfigurationError("System configuration error: No default LLM module found.")

        # 2. 构建健壮的默认配置
        default_config = AgentConfig(
            model_params=ModelParams(),
            io_config=InputOutputConfig(),
            rag_config=AgentRAGConfig(enabled=False),
            deep_memory=DeepMemoryConfig(enabled=False, summary_model_uuid=default_llm.uuid)
        )

        # limit_feature = await self.feature_dao.get_by_name("limit:agent:custom:execution")
        limit_feature = None
        
        instance = Agent(
            version_tag="__workspace__",
            status=VersionStatus.WORKSPACE,
            creator_id=actor.id,
            resource_type=self.name,
            name=resource.name,
            description=resource.description,
            resource=resource,
            agent_config=default_config.model_dump(mode='json'),
            system_prompt="You are a helpful AI assistant.",
            llm_module_version_id=default_llm.id,
            linked_feature_id=limit_feature.id if limit_feature else None
        )
        return instance

    async def update_instance(self, instance: ResourceInstance, update_data: Dict[str, Any]) -> Agent:
        if not isinstance(instance, Agent): raise ServiceException("Not an Agent")
        
        try:
            validated = AgentUpdate.model_validate(update_data)
        except Exception as e:
            raise ServiceException(f"Invalid update data: {e}")

        data = validated.model_dump(exclude_unset=True)
        
        if "llm_module_version_uuid" in data:
            uuid_val = data.pop("llm_module_version_uuid")
            if uuid_val:
                smv = await self.module_service.smv_dao.get_one(where={"uuid": uuid_val})
                if not smv:
                    raise NotFoundError(f"LLM Module Version {uuid_val} not found.")
                # 这里可以加更多检查，比如类型是否为 LLM
                instance.llm_module_version_id = smv.id
            # Else ignore or handle unbind logic

        if "agent_config" in data:
            current_config = AgentConfig(**instance.agent_config)
            updated_config = current_config.model_copy(update=data["agent_config"])
            instance.agent_config = updated_config.model_dump(mode='json')
            data.pop("agent_config")

        for k, v in data.items():
            if hasattr(instance, k):
                setattr(instance, k, v)
        
        return instance

    async def delete_instance(self, instance: Agent) -> None:
        await self.db.delete(instance)

    async def on_resource_delete(self, resource: Resource) -> None:
        pass

    async def publish_instance(self, workspace_instance: Agent, version_tag: str, version_notes: Optional[str], actor: User) -> Agent:
        snapshot = Agent(
            resource_id=workspace_instance.resource_id,
            status=VersionStatus.PUBLISHED,
            version_tag=version_tag,
            version_notes=version_notes,
            creator_id=actor.id,
            published_at=func.now(),
            name=workspace_instance.name,
            description=workspace_instance.description,
            system_prompt=workspace_instance.system_prompt,
            agent_config=workspace_instance.agent_config,
            llm_module_version_id=workspace_instance.llm_module_version_id
        )
        return snapshot

    async def validate_instance(self, instance: Agent) -> ValidationResult:
        errors = []
        if not instance.llm_module_version_id:
            errors.append("Must select an LLM model.")
        try:
            AgentConfig(**instance.agent_config)
        except Exception as e:
            errors.append(f"Invalid configuration: {str(e)}")
        return ValidationResult(is_valid=not errors, errors=errors)

    async def get_dependencies(self, instance: Agent) -> List[DependencyInfo]:
        refs = await self.ref_dao.get_dependencies(instance.id)
        return [
            DependencyInfo(
                resource_uuid=ref.target_resource.uuid,
                instance_uuid=ref.target_instance.uuid,
                alias=ref.alias
            ) for ref in refs
        ]

    async def get_searchable_content(self, instance: Agent) -> str:
        return f"{instance.name} {instance.description or ''} {instance.system_prompt or ''}"

    async def serialize_instance(self, instance: Agent) -> Dict[str, Any]:
        data = AgentRead.model_validate(instance).model_dump()
        data["llm_module_version_uuid"] = instance.llm_module_version.uuid
        return data

    async def as_llm_tool(self, instance: Agent) -> Optional[LLMTool]:
        return LLMTool(
            type="function",
            function={
                "name": f"call_agent_{instance.uuid.replace('-', '_')}",
                "description": instance.description or f"Ask agent {instance.name}",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The question to ask the agent."}
                    },
                    "required": ["query"]
                }
            }
        )
