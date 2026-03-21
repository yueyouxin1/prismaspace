# src/app/services/resource/agent/agent_service.py

import json
import logging
import uuid
import asyncio
from typing import Dict, Any, List, Callable, Optional, AsyncGenerator, Union, Set
from contextlib import asynccontextmanager, nullcontext
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from app.core.context import AppContext
from app.core.trace_manager import TraceManager
from app.db.session import SessionLocal
from app.utils.async_generator import AsyncGeneratorManager
from app.models import (
    User,
    Team,
    Workspace,
    Resource,
    ResourceInstance,
    ResourceRef,
    VersionStatus,
    ServiceModuleVersion,
    ResourceExecution,
    ResourceExecutionStatus,
)
from app.models.resource.agent import Agent, AgentMessage, AgentMessageRole
from app.dao.resource.agent.agent_dao import AgentDao
from app.dao.module.service_module_dao import ServiceModuleVersionDao
from app.dao.resource.resource_ref_dao import ResourceRefDao
from app.dao.product.feature_dao import FeatureDao
from app.dao.workspace.workspace_dao import WorkspaceDao

# Schemas
from app.schemas.resource.agent.agent_schemas import (
    AgentUpdate, AgentRead, AgentConfig, GenerationDiversity, AgentRAGConfig, ModelParams,
    InputOutputConfig, DeepMemoryConfig, AgentExecutionRequest, AgentExecutionResponse,
    AgentRunDetailRead, AgentRunEventRead, AgentRunSummaryRead, AgentToolExecutionRead,
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
from app.services.resource.agent.persisting_callbacks import PersistingAgentCallbacks
from app.services.resource.agent.processors import ResourceAwareToolExecutor, ShortContextProcessor
from app.services.resource.agent.protocol_adapter import AgUiProtocolAdapter, ProtocolAdapterRegistry
from app.services.resource.agent.protocol_adapter.base import ProtocolAdaptedRun
from app.services.resource.agent.live_events import AgentLiveEventService
from app.services.resource.agent.run_control import AgentRunControlService, AgentRunRegistry
from app.services.resource.agent.run_persistence import AgentRunPersistenceService
from app.services.resource.agent.run_preparation import AgentRunPreparationService
from app.services.resource.agent.run_execution import AgentRunExecutionService
from app.services.resource.agent.run_query import AgentRunQueryService
from app.schemas.resource.execution_schemas import AnyExecutionRequest, AnyExecutionResponse
from app.services.exceptions import ServiceException, NotFoundError, ConfigurationError, PermissionDeniedError
from app.services.product.types.feature import FeatureRole
from app.services.resource.agent.types.agent import AgentRunResult, AgentStreamMessageIds, PreparedAgentRun
from app.services.resource.execution.execution_ledger_service import ExecutionLedgerService
from app.utils.id_generator import generate_uuid

# Engine
from app.engine.agent import (
    AgentEngineService, AgentInput, AgentStep, AgentResult, AgentClientToolCall, AgentEngineCallbacks, BaseToolExecutor,
    AgentRuntimeCheckpoint,
)
from app.engine.model.llm import (
    LLMEngineService, LLMProviderConfig, LLMRunConfig, LLMMessage, LLMTool, LLMToolCall, LLMToolCallChunk, LLMUsage, LLMEngineCallbacks
)
from app.engine.utils.tokenizer.manager import tokenizer_manager
from ag_ui.core import (
    EventType,
    RawEvent,
)

logger = logging.getLogger(__name__)

@register_service
class AgentService(ResourceImplementationService):
    name: str = "agent"

    def __init__(self, context: AppContext):
        super().__init__(context)
        self.dao = AgentDao(context.db)
        self.ref_dao = ResourceRefDao(context.db)
        self.feature_dao = FeatureDao(context.db)
        self.workspace_dao = WorkspaceDao(context.db)
        self.redis = context.redis_service
        self.module_service = ServiceModuleService(context)
        self.ai_provider = AICapabilityProvider(context)
        self.agent_memory_var_service = AgentMemoryVarService(context)
        self.long_term_service = LongTermContextService(context)
        self.prompt_template = PromptTemplate()
        self.protocol_adapters = ProtocolAdapterRegistry()
        self.execution_ledger_service = ExecutionLedgerService(context)
        self.live_event_service = AgentLiveEventService(context)
        self.run_control_service = AgentRunControlService(context)
        self.run_persistence_service = AgentRunPersistenceService(context)
        self.protocol_adapters.register("ag-ui", AgUiProtocolAdapter())
        self.resource_resolver = BaseResourceService(context)
        self._db_session_factory = context.db_session_factory or SessionLocal

    def _preparation_service(self) -> AgentRunPreparationService:
        return AgentRunPreparationService(self)

    def _execution_service(self) -> AgentRunExecutionService:
        return AgentRunExecutionService(self)

    def _query_service(self) -> AgentRunQueryService:
        return AgentRunQueryService(self)

    @staticmethod
    def _parse_agent_config(instance: Agent) -> AgentConfig:
        return AgentConfig(**instance.agent_config)

    def _build_pipeline_manager(
        self,
        *,
        rendered_system_prompt: str,
        user_message: LLMMessage,
        history_messages: List[LLMMessage],
        tool_executor,
        agent_config: AgentConfig,
        dependencies: List[ResourceRef],
        runtime_workspace: Workspace,
        session_manager,
        prompt_variables: Dict[str, Any],
    ) -> AgentPipelineManager:
        return AgentPipelineManager(
            system_message=LLMMessage(role="system", content=rendered_system_prompt),
            user_message=user_message,
            history=history_messages,
            tool_executor=tool_executor,
        ).add_standard_processors(
            app_context=self.context,
            agent_config=agent_config,
            dependencies=dependencies,
            runtime_workspace=runtime_workspace,
            session_manager=session_manager,
            prompt_variables=prompt_variables,
        )

    # ==========================================================================
    # Execution Logic (The Core)
    # ==========================================================================

    @staticmethod
    def _event_to_payload(event: Any) -> Dict[str, Any]:
        if hasattr(event, "model_dump"):
            return event.model_dump(mode="json", by_alias=True, exclude_none=True)
        if isinstance(event, dict):
            return event
        return RawEvent(
            type=EventType.RAW,
            event=str(event),
            source="prismaspace.agent",
        ).model_dump(mode="json", by_alias=True, exclude_none=True)

    @staticmethod
    def _normalize_uuid(value: Optional[str]) -> Optional[str]:
        if not isinstance(value, str):
            return None
        candidate = value.strip()
        if not candidate:
            return None
        try:
            normalized = str(uuid.UUID(candidate))
        except ValueError:
            return None
        return normalized if candidate.lower() == normalized else None

    @classmethod
    def _is_valid_uuid(cls, value: Optional[str]) -> bool:
        return cls._normalize_uuid(value) is not None

    @classmethod
    def _normalize_thread_id(cls, thread_id: Optional[str]) -> str:
        if not isinstance(thread_id, str):
            return ""
        stripped = thread_id.strip()
        if not stripped:
            return ""
        return cls._normalize_uuid(stripped) or stripped

    @classmethod
    def _normalize_parent_run_id(cls, parent_run_id: Optional[str]) -> Optional[str]:
        return cls._normalize_uuid(parent_run_id)

    @classmethod
    def _normalize_interrupt_id(cls, interrupt_id: Optional[str]) -> Optional[str]:
        return cls._normalize_uuid(interrupt_id)

    @staticmethod
    def build_interrupt_id(run_id: str) -> str:
        return run_id

    @staticmethod
    def _restore_adapted_from_checkpoint(
        adapted: ProtocolAdaptedRun,
        checkpoint: Optional[Any],
    ) -> ProtocolAdaptedRun:
        if checkpoint is None or not isinstance(getattr(checkpoint, "adapted_snapshot", None), dict):
            return adapted

        snapshot = checkpoint.adapted_snapshot
        custom_history = adapted.custom_history
        if not custom_history:
            custom_history = [
                item if isinstance(item, LLMMessage) else LLMMessage.model_validate(item)
                for item in (snapshot.get("custom_history") or [])
            ]
        resume_messages = adapted.resume_messages or [
            item if isinstance(item, LLMMessage) else LLMMessage.model_validate(item)
            for item in (snapshot.get("resume_messages") or [])
        ]
        has_custom_history = adapted.has_custom_history or bool(snapshot.get("has_custom_history"))

        return ProtocolAdaptedRun(
            input_content=adapted.input_content,
            thread_id=adapted.thread_id,
            client_tools=adapted.client_tools,
            custom_history=custom_history,
            resume_messages=resume_messages,
            has_custom_history=has_custom_history,
            resume_tool_call_ids=adapted.resume_tool_call_ids,
            resume_interrupt_id=adapted.resume_interrupt_id,
        )

    @staticmethod
    def _restore_runtime_checkpoint(
        runtime_snapshot: Optional[Dict[str, Any]],
    ) -> Optional[AgentRuntimeCheckpoint]:
        if not isinstance(runtime_snapshot, dict) or not runtime_snapshot:
            return None
        try:
            checkpoint = AgentRuntimeCheckpoint.model_validate(runtime_snapshot)
        except Exception:
            logger.warning("Failed to restore runtime checkpoint from snapshot.", exc_info=True)
            return None
        if not checkpoint.messages:
            return None
        return checkpoint

    @staticmethod
    def _resolve_session_mode(run_input: RunAgentInputExt) -> str:
        platform = run_input.platform_props
        candidate = platform.session_mode if platform else None
        if not candidate:
            return "auto"
        return candidate

    @classmethod
    def _requires_persistent_session_binding(
        cls,
        run_input: RunAgentInputExt,
        requested_thread_id: Optional[str] = None,
    ) -> bool:
        session_mode = cls._resolve_session_mode(run_input)
        if session_mode == "stateless":
            return False
        if session_mode == "stateful":
            return True

        session_candidate = requested_thread_id if requested_thread_id is not None else run_input.thread_id
        return cls._is_valid_uuid(session_candidate)

    @staticmethod
    def _resolve_protocol_name(run_input: RunAgentInputExt) -> str:
        platform = run_input.platform_props
        candidate = platform.protocol if platform else None
        if not candidate:
            return "ag-ui"
        return candidate

    @staticmethod
    def _parse_positive_int(value: Any) -> Optional[int]:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value > 0 else None
        if isinstance(value, float):
            ivalue = int(value)
            return ivalue if ivalue > 0 else None
        if isinstance(value, str):
            text = value.strip()
            if text.isdigit():
                ivalue = int(text)
                return ivalue if ivalue > 0 else None
        return None

    @classmethod
    def _resolve_model_context_window(cls, model_attributes: Any) -> int:
        fallback = 8192
        if not isinstance(model_attributes, dict):
            logger.warning(
                "LLM module attributes are invalid or missing; falling back max_context_window=%s",
                fallback,
            )
            return fallback

        resolved = cls._parse_positive_int(model_attributes.get("context_window"))
        if resolved:
            return resolved

        logger.warning(
            "LLM module attributes do not define valid 'context_window'; falling back max_context_window=%s",
            fallback,
        )
        return fallback

    @classmethod
    def _build_stream_message_ids(cls) -> AgentStreamMessageIds:
        return AgentStreamMessageIds(
            user_message_id=generate_uuid(),
            assistant_message_id=generate_uuid(),
            reasoning_message_id=generate_uuid(),
            activity_message_id=generate_uuid(),
        )

    async def _resolve_runtime_workspace(
        self,
        *,
        instance: Agent,
        runtime_workspace: Optional[Workspace],
    ) -> Workspace:
        if runtime_workspace and getattr(runtime_workspace, "id", None) is not None:
            resolved = await self.workspace_dao.get_by_pk(runtime_workspace.id)
            if resolved:
                return resolved
        return instance.resource.workspace

    @staticmethod
    def _extract_pending_tool_call_ids(messages: List[AgentMessage]) -> Set[str]:
        if not messages:
            return set()

        resolved_tool_call_ids: Set[str] = set()
        pending_tool_call_ids: Set[str] = set()

        for message in reversed(messages):
            role = getattr(message, "role", None)
            role_value = role.value if hasattr(role, "value") else str(role or "")

            if role_value == AgentMessageRole.TOOL.value:
                tool_call_id = getattr(message, "tool_call_id", None)
                if isinstance(tool_call_id, str) and tool_call_id:
                    resolved_tool_call_ids.add(tool_call_id)
                continue

            if role_value != AgentMessageRole.ASSISTANT.value:
                continue

            tool_calls = getattr(message, "tool_calls", None)
            if not isinstance(tool_calls, list):
                continue

            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                tool_call_id = tool_call.get("id")
                if (
                    isinstance(tool_call_id, str)
                    and tool_call_id
                    and tool_call_id not in resolved_tool_call_ids
                ):
                    pending_tool_call_ids.add(tool_call_id)

        return pending_tool_call_ids

    async def _enforce_pending_tool_results(
        self,
        session_manager: AgentSessionManager,
        resume_tool_call_ids: List[str],
    ) -> None:
        if not session_manager.session:
            return

        # 协议上未闭合的 client tool call 只允许停留在最新的中断轮次。
        recent_messages = await session_manager.get_recent_messages(1)
        pending_tool_call_ids = self._extract_pending_tool_call_ids(recent_messages)
        if not pending_tool_call_ids:
            return

        provided_tool_call_ids = {
            tool_call_id.strip()
            for tool_call_id in (resume_tool_call_ids or [])
            if isinstance(tool_call_id, str) and tool_call_id.strip()
        }
        missing_tool_call_ids = pending_tool_call_ids - provided_tool_call_ids
        if not missing_tool_call_ids:
            return

        raise ServiceException(
            "Pending client tool results are required for toolCallIds: "
            + ", ".join(sorted(missing_tool_call_ids))
        )

    def _buffer_protocol_history_messages(
        self,
        session_manager: AgentSessionManager,
        history: List[LLMMessage],
    ) -> None:
        if not session_manager.session or not history:
            return

        for message in history:
            if not isinstance(message, LLMMessage):
                continue
            mapped_role = {
                "system": AgentMessageRole.SYSTEM,
                "user": AgentMessageRole.USER,
                "assistant": AgentMessageRole.ASSISTANT,
                "tool": AgentMessageRole.TOOL,
            }.get(message.role)
            if not mapped_role:
                continue

            reasoning_content: Optional[str] = None
            text_content: Optional[str] = message.content if isinstance(message.content, str) else None
            content_parts = message.content if isinstance(message.content, list) else None

            if (
                mapped_role == AgentMessageRole.SYSTEM
                and isinstance(text_content, str)
                and text_content.startswith("[CONTEXT]\n")
            ):
                continue

            if (
                mapped_role == AgentMessageRole.SYSTEM
                and isinstance(text_content, str)
                and text_content.startswith("[REASONING]\n")
            ):
                mapped_role = AgentMessageRole.REASONING
                reasoning_content = text_content[len("[REASONING]\n") :].strip()
                text_content = reasoning_content

            if mapped_role == AgentMessageRole.TOOL and text_content is None and content_parts is not None:
                text_content = json.dumps(content_parts, ensure_ascii=False)

            if (
                text_content is None
                and not content_parts
                and not message.tool_calls
                and not message.tool_call_id
                and not message.encrypted_value
                and not reasoning_content
            ):
                continue

            session_manager.buffer_message(
                role=mapped_role,
                text_content=text_content,
                content_parts=content_parts,
                reasoning_content=reasoning_content,
                encrypted_value=message.encrypted_value,
                tool_calls=message.tool_calls,
                tool_call_id=message.tool_call_id,
            )

    async def sync_execute(
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
                    "threadId": getattr(stream_result, "thread_id", None) or run_input.thread_id,
                    "runId": getattr(stream_result, "run_id", run_input.run_id),
                    "code": "AGENT_RUNTIME_ERROR",
                    "message": str(exc),
                    "retriable": False,
                }
            )

        return RunEventsResponse(
            threadId=getattr(stream_result, "thread_id", None) or run_input.thread_id,
            runId=getattr(stream_result, "run_id", run_input.run_id),
            events=events,
        )

    async def execute(
        self,
        instance_uuid: str,
        execute_params: AnyExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None
    ) -> AnyExecutionResponse:
        if not isinstance(execute_params, AgentExecutionRequest):
            raise ServiceException("Agent execute expects AgentExecutionRequest as execute_params.")
        run_events = await self.sync_execute(
            instance_uuid=instance_uuid,
            run_input=execute_params.inputs,
            actor=actor,
            runtime_workspace=runtime_workspace,
        )
        return AgentExecutionResponse(data=run_events)

    async def execute_batch(
        self,
        instance_uuids: List[str],
        execute_params: AnyExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None
    ) -> List[AnyExecutionResponse]:
        """
        Agent 暂不支持真正的并行 Batch（每个都是独立的有状态循环）。
        简单实现为循环调用。
        """
        if not isinstance(execute_params, AgentExecutionRequest):
            raise ServiceException("Agent execute_batch expects AgentExecutionRequest as execute_params.")
        results = []
        for uuid in instance_uuids:
            res = await self.execute(uuid, execute_params, actor, runtime_workspace)
            results.append(res)
        
        return results

    async def async_execute(
        self, 
        instance_uuid: str, 
        run_input: RunAgentInputExt,
        actor: User,
        runtime_workspace: Optional[Workspace] = None
    ) -> AgentRunResult:
        from app.services.resource.agent.runtime_runner import AgentRuntimeRunner

        runner = AgentRuntimeRunner(
            base_context=self.context,
            db_session_factory=self._db_session_factory,
        )
        return await runner.start(
            instance_uuid=instance_uuid,
            run_input=run_input,
            actor=actor,
            runtime_workspace=runtime_workspace,
        )

    async def _prepare_async_run(
        self,
        *,
        instance_uuid: str,
        run_input: RunAgentInputExt,
        actor: User,
        runtime_workspace: Optional[Workspace] = None,
    ) -> PreparedAgentRun:
        return await self._preparation_service().prepare_async_run(
            instance_uuid=instance_uuid,
            run_input=run_input,
            actor=actor,
            runtime_workspace=runtime_workspace,
        )

    @asynccontextmanager
    async def _session_lock(self, session_uuid: str):
        """
        [Concurrency Guard] 分布式锁，防止同一个会话并发写入。
        """
        lock_key = f"lock:session:{session_uuid}"
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
        runtime_workspace: Workspace,
        trace_manager: TraceManager,
        generator_manager: AsyncGeneratorManager,
        execution: ResourceExecution,
        turn_id: str,
        session_manager: Optional[AgentSessionManager] = None,
        run_input: Optional[RunAgentInputExt] = None,
        message_ids: Optional[AgentStreamMessageIds] = None,
        dependencies: Optional[List[ResourceRef]] = None,
        adapted: Optional[ProtocolAdaptedRun] = None,
        tool_executor: Optional[ResourceAwareToolExecutor] = None,
        agent_instance: Optional[Agent] = None,
        resume_checkpoint: Optional[AgentRuntimeCheckpoint] = None,
    ):
        await self._execution_service().run_background_task(
            agent_config=agent_config,
            llm_module_version=llm_module_version,
            runtime_workspace=runtime_workspace,
            trace_manager=trace_manager,
            generator_manager=generator_manager,
            execution=execution,
            turn_id=turn_id,
            session_manager=session_manager,
            run_input=run_input,
            message_ids=message_ids,
            dependencies=dependencies,
            adapted=adapted,
            tool_executor=tool_executor,
            agent_instance=agent_instance,
            resume_checkpoint=resume_checkpoint,
        )

    async def _should_cancel_run(self, run_id: str) -> bool:
        control = getattr(self, "run_control_service", None)
        if control is None:
            return False
        return await control.should_cancel(run_id)

    async def _clear_cancel_run(self, run_id: str) -> None:
        control = getattr(self, "run_control_service", None)
        if control is None:
            return None
        await control.clear_cancel(run_id)

    async def _persist_agent_run_artifacts(
        self,
        *,
        execution: ResourceExecution,
        agent_instance: Optional[Agent],
        session: Optional[Any],
        turn_id: Optional[str],
        callbacks: Optional[PersistingAgentCallbacks],
    ) -> None:
        persistence = getattr(self, "run_persistence_service", None)
        if callbacks is None or agent_instance is None or persistence is None:
            return
        try:
            await persistence.append_events(
                execution=execution,
                agent_instance=agent_instance,
                session_id=getattr(session, "id", None),
                events=callbacks.get_captured_events(),
            )
            await persistence.upsert_tool_histories(
                execution=execution,
                agent_instance=agent_instance,
                session_id=getattr(session, "id", None),
                turn_id=turn_id,
                histories=callbacks.get_tool_history(),
            )
            await self.db.commit()
        except Exception as exc:
            logger.error("Failed to persist agent run artifacts for run %s: %s", execution.run_id, exc, exc_info=True)
            await self.db.rollback()

    async def _upsert_run_checkpoint(
        self,
        *,
        execution: ResourceExecution,
        agent_instance: Optional[Agent],
        session: Optional[Any],
        thread_id: str,
        turn_id: str,
        checkpoint_kind: str,
        run_input: Optional[RunAgentInputExt],
        adapted: ProtocolAdaptedRun,
        runtime_snapshot: Dict[str, Any],
        pending_client_tool_calls: List[Dict[str, Any]],
    ) -> None:
        persistence = getattr(self, "run_persistence_service", None)
        if persistence is None or agent_instance is None:
            return
        await persistence.upsert_checkpoint(
            execution=execution,
            agent_instance=agent_instance,
            session_id=getattr(session, "id", None),
            thread_id=thread_id,
            turn_id=turn_id,
            checkpoint_kind=checkpoint_kind,
            run_input_payload=run_input.model_dump(mode="json", by_alias=True, exclude_none=True) if run_input else {},
            adapted=adapted,
            runtime_snapshot=runtime_snapshot,
            pending_client_tool_calls=pending_client_tool_calls,
        )
        await self.db.commit()

    async def _delete_run_checkpoint(self, execution_id: int) -> None:
        persistence = getattr(self, "run_persistence_service", None)
        if persistence is None:
            return
        await persistence.delete_checkpoint(execution_id=execution_id)
        await self.db.commit()

    async def list_runs(
        self,
        instance_uuid: str,
        *,
        limit: int = 20,
    ) -> List[AgentRunSummaryRead]:
        return await self._query_service().list_runs(instance_uuid, limit=limit)

    async def get_run(self, run_id: str) -> AgentRunDetailRead:
        return await self._query_service().get_run(run_id)

    async def list_run_events(self, run_id: str, *, limit: int = 1000) -> List[AgentRunEventRead]:
        return await self._query_service().list_run_events(run_id, limit=limit)

    async def cancel_run(self, run_id: str) -> Dict[str, Any]:
        return await self._query_service().cancel_run(run_id)

    async def get_active_run(self, instance_uuid: str, actor: User, thread_id: str) -> Optional[AgentRunSummaryRead]:
        return await self._query_service().get_active_run(
            instance_uuid=instance_uuid,
            actor=actor,
            thread_id=thread_id,
        )

    async def stream_live_run_events(self, run_id: str, *, after_seq: int = 0):
        async for envelope in self._query_service().stream_live_events(run_id=run_id, after_seq=after_seq):
            yield envelope

    # --- CRUD Implementation ---

    async def get_by_uuid(self, instance_uuid: str) -> Optional[Agent]:
        return await self.dao.get_by_uuid(instance_uuid)

    async def get_runtime_by_uuid(self, instance_uuid: str) -> Optional[Agent]:
        return await self.dao.get_runtime_by_uuid(instance_uuid)

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
