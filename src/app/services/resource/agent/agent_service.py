# src/app/services/resource/agent/agent_service.py

import json
import logging
import uuid
import asyncio
import time
from typing import Dict, Any, List, Callable, Optional, AsyncGenerator, Union, Set
from decimal import Decimal
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
    InputOutputConfig, DeepMemoryConfig, AgentExecutionRequest, AgentExecutionResponse
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
from app.services.resource.agent.processors import ResourceAwareToolExecutor, ShortContextProcessor
from app.services.resource.agent.protocol_adapter import AgUiProtocolAdapter, ProtocolAdapterRegistry
from app.services.resource.agent.protocol_adapter.base import ProtocolAdaptedRun
from app.schemas.resource.execution_schemas import AnyExecutionRequest, AnyExecutionResponse
from app.services.exceptions import ServiceException, NotFoundError, ConfigurationError, PermissionDeniedError
from app.services.product.types.feature import FeatureRole
from app.services.resource.agent.types.agent import AgentRunResult, AgentStreamMessageIds
from app.services.resource.execution.execution_ledger_service import ExecutionLedgerService
from app.utils.id_generator import generate_uuid

# Engine
from app.engine.agent import (
    AgentEngineService, AgentInput, AgentStep, AgentResult, AgentClientToolCall, AgentEngineCallbacks, BaseToolExecutor
)
from app.engine.model.llm import (
    LLMEngineService, LLMProviderConfig, LLMRunConfig, LLMMessage, LLMTool, LLMToolCall, LLMToolCallChunk, LLMUsage, LLMEngineCallbacks
)
from app.engine.utils.tokenizer.manager import tokenizer_manager
from ag_ui.core import (
    ActivityDeltaEvent,
    ActivitySnapshotEvent,
    CustomEvent,
    EventType,
    MessagesSnapshotEvent,
    RawEvent,
    ReasoningEndEvent,
    ReasoningEncryptedValueEvent,
    ReasoningMessageContentEvent,
    ReasoningMessageEndEvent,
    ReasoningMessageStartEvent,
    ReasoningStartEvent,
    RunErrorEvent,
    RunStartedEvent,
    StepFinishedEvent,
    StepStartedEvent,
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
        run_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        run_input: Optional[RunAgentInputExt] = None,
        message_ids: Optional[AgentStreamMessageIds] = None,
    ):
        self.trace_id = trace_id
        self.session_manager = session_manager
        self.generator_manager = generator_manager
        self.usage_accumulator = usage_accumulator
        self.run_input = run_input
        canonical_run_id = run_id or (run_input.run_id if run_input else None)
        if run_input and canonical_run_id and run_input.run_id != canonical_run_id:
            raise ValueError("PersistingAgentCallbacks run_id must match run_input.run_id.")
        self.run_id = canonical_run_id
        self.turn_id = turn_id or canonical_run_id
        self.input_payload = (
            run_input.model_dump(mode="json", by_alias=True, exclude_none=True)
            if run_input
            else {}
        )
        self.messages_snapshot = self.input_payload.get("messages", []) if isinstance(self.input_payload, dict) else []
        self.final_result: Optional[AgentResult] = None
        self.pending_terminal_event: Optional[Dict[str, Any]] = None
        self.has_terminal_event = False
        self.user_message_id = message_ids.user_message_id if message_ids else generate_uuid()
        self.assistant_message_id = message_ids.assistant_message_id if message_ids else generate_uuid()
        self.reasoning_message_id = message_ids.reasoning_message_id if message_ids else generate_uuid()
        self.activity_message_id = message_ids.activity_message_id if message_ids else generate_uuid()
        self.activity_initialized = False
        self.activity_state: Dict[str, Any] = {"status": "running", "tools": {}}
        self.text_started = False
        self.reasoning_started = False
        self.tool_call_stream_states: Dict[str, Dict[str, Any]] = {}
        self.tool_call_index_states: Dict[int, Dict[str, Any]] = {}
        self._emitted_encrypted_entities: Set[str] = set()

    def _base_meta(self) -> Dict[str, Any]:
        thread_id = (
            self.session_manager.session.uuid
            if self.session_manager and self.session_manager.session
            else (self.run_input.thread_id if self.run_input else None)
        )
        return {
            "runId": self.run_id,
            "traceId": self.trace_id,
            "threadId": thread_id,
            "turnId": self.turn_id,
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

    async def _emit_reasoning_encrypted_value(
        self,
        *,
        subtype: str,
        entity_id: str,
        encrypted_value: Optional[str],
    ) -> None:
        encrypted_text = encrypted_value.strip() if isinstance(encrypted_value, str) else ""
        if not encrypted_text or not entity_id:
            return
        entity_key = f"{subtype}:{entity_id}"
        if entity_key in self._emitted_encrypted_entities:
            return
        self._emitted_encrypted_entities.add(entity_key)
        await self._emit(
            ReasoningEncryptedValueEvent(
                type=EventType.REASONING_ENCRYPTED_VALUE,
                subtype=subtype,
                entity_id=entity_id,
                encrypted_value=encrypted_text,
            )
        )

    @staticmethod
    def _json_pointer_escape(value: str) -> str:
        return value.replace("~", "~0").replace("/", "~1")

    async def _emit_activity_snapshot_if_needed(self):
        if not self.run_input or self.activity_initialized:
            return
        self.activity_initialized = True
        await self._emit(
            ActivitySnapshotEvent(
                type=EventType.ACTIVITY_SNAPSHOT,
                message_id=self.activity_message_id,
                activity_type="tool_call_timeline",
                content=self.activity_state,
                replace=True,
            )
        )

    async def _emit_activity_delta(self, patch: List[Dict[str, Any]]):
        if not self.activity_initialized or not patch:
            return
        await self._emit(
            ActivityDeltaEvent(
                type=EventType.ACTIVITY_DELTA,
                message_id=self.activity_message_id,
                activity_type="tool_call_timeline",
                patch=patch,
            )
        )

    async def _set_activity_status(self, status: str):
        if not self.activity_initialized:
            return
        self.activity_state["status"] = status
        await self._emit_activity_delta(
            [{"op": "replace", "path": "/status", "value": status}]
        )

    async def _upsert_activity_tool(self, tool_call_id: str, tool_name: str):
        if not self.activity_initialized or not tool_call_id:
            return
        tools = self.activity_state["tools"]
        if tool_call_id not in tools:
            tools[tool_call_id] = {
                "name": tool_name,
                "args": "",
                "status": "started",
                "result": None,
            }
            escaped = self._json_pointer_escape(tool_call_id)
            await self._emit_activity_delta(
                [{"op": "add", "path": f"/tools/{escaped}", "value": tools[tool_call_id]}]
            )
            return
        tools[tool_call_id]["name"] = tool_name or tools[tool_call_id].get("name", "")
        tools[tool_call_id]["status"] = "started"
        escaped = self._json_pointer_escape(tool_call_id)
        await self._emit_activity_delta(
            [
                {"op": "replace", "path": f"/tools/{escaped}/name", "value": tools[tool_call_id]["name"]},
                {"op": "replace", "path": f"/tools/{escaped}/status", "value": "started"},
            ]
        )

    async def _append_activity_tool_args(self, tool_call_id: str, delta: str):
        if not self.activity_initialized or not tool_call_id:
            return
        if tool_call_id not in self.activity_state["tools"]:
            await self._upsert_activity_tool(tool_call_id, "")
        tool_state = self.activity_state["tools"][tool_call_id]
        tool_state["args"] = f"{tool_state.get('args', '')}{delta}"
        escaped = self._json_pointer_escape(tool_call_id)
        await self._emit_activity_delta(
            [{"op": "replace", "path": f"/tools/{escaped}/args", "value": tool_state["args"]}]
        )

    async def _finalize_activity_tool(self, tool_call_id: str, status: str, result: Optional[str] = None):
        if not self.activity_initialized or not tool_call_id:
            return
        if tool_call_id not in self.activity_state["tools"]:
            await self._upsert_activity_tool(tool_call_id, "")
        tool_state = self.activity_state["tools"][tool_call_id]
        tool_state["status"] = status
        escaped = self._json_pointer_escape(tool_call_id)
        patch: List[Dict[str, Any]] = [
            {"op": "replace", "path": f"/tools/{escaped}/status", "value": status}
        ]
        if result is not None:
            if tool_state.get("result") is None:
                patch.append({"op": "add", "path": f"/tools/{escaped}/result", "value": result})
            else:
                patch.append({"op": "replace", "path": f"/tools/{escaped}/result", "value": result})
            tool_state["result"] = result
        await self._emit_activity_delta(patch)

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
        await self._upsert_activity_tool(tool_call_id, tool_name)

    async def _emit_tool_call_args(self, tool_call_id: str, delta: str):
        await self._emit(
            ToolCallArgsEvent(
                type=EventType.TOOL_CALL_ARGS,
                tool_call_id=tool_call_id,
                delta=delta,
            )
        )
        await self._append_activity_tool_args(tool_call_id, delta)

    async def _emit_tool_call_end(self, tool_call_id: str):
        await self._emit(
            ToolCallEndEvent(
                type=EventType.TOOL_CALL_END,
                tool_call_id=tool_call_id,
            )
        )
        await self._finalize_activity_tool(tool_call_id, "awaiting_result")

    async def _close_open_messages(self):
        for state in list(self.tool_call_stream_states.values()):
            tool_call_id = state.get("tool_call_id")
            if tool_call_id and state.get("started") and not state.get("ended"):
                await self._emit_tool_call_end(tool_call_id=tool_call_id)
                state["ended"] = True
        self.tool_call_stream_states.clear()
        self.tool_call_index_states.clear()

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

    async def _emit_result_reasoning_if_needed(self, result: AgentResult):
        reasoning_text = (result.reasoning_content or "").strip()
        if not reasoning_text or self.reasoning_started:
            return
        await self.on_reasoning_chunk_generated(reasoning_text)

    async def on_agent_start(self):
        if not self.run_input:
            return
        await self._emit(
            RunStartedEvent(
                type=EventType.RUN_STARTED,
                thread_id=self.run_input.thread_id,
                run_id=self.run_id,
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
        await self._emit_activity_snapshot_if_needed()
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

            await self._emit_reasoning_encrypted_value(
                subtype="tool-call",
                entity_id=tool_call_id,
                encrypted_value=tool_call.get("encryptedValue") or tool_call.get("encrypted_value"),
            )

        # 一轮工具调用收口后清理状态，避免跨轮污染。
        self.tool_call_stream_states.clear()
        self.tool_call_index_states.clear()

        if self.session_manager:
            self.session_manager.buffer_message(role=AgentMessageRole.ASSISTANT, tool_calls=tool_calls_data)

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
        step_name = step.action.function.get("name", "") if isinstance(step.action.function, dict) else ""
        step_name = step_name or step.action.id
        await self._emit(
            StepStartedEvent(
                type=EventType.STEP_STARTED,
                step_name=step_name,
            )
        )
        if step.thought and self.session_manager:
            self.session_manager.buffer_message(
                role=AgentMessageRole.REASONING,
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
        await self._finalize_activity_tool(step.action.id, "completed", result=output_text)
        await self._emit(
            StepFinishedEvent(
                type=EventType.STEP_FINISHED,
                step_name=step_name,
            )
        )
        if self.session_manager:
            self.session_manager.buffer_message(
                role=AgentMessageRole.TOOL,
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
        encrypted_value = result.message.encrypted_value
        if not text_content and not content_parts and not reasoning_content and not encrypted_value:
            return
        self.session_manager.buffer_message(
            role=AgentMessageRole.ASSISTANT,
            message_uuid=self.assistant_message_id,
            text_content=text_content,
            content_parts=content_parts,
            reasoning_content=reasoning_content,
            encrypted_value=encrypted_value,
        )

    async def _prepare_terminal_event(
        self,
        *,
        result: AgentResult,
        outcome: str,
        activity_status: str,
        interrupt: Optional[AgUiInterrupt] = None,
        run_state: Optional[str] = None,
    ) -> None:
        self.final_result = result
        if self.has_terminal_event or self.pending_terminal_event:
            return
        await self._emit_result_reasoning_if_needed(result)
        await self._close_open_messages()
        await self._persist_assistant(result)
        await self._emit_reasoning_encrypted_value(
            subtype="message",
            entity_id=self.assistant_message_id,
            encrypted_value=result.message.encrypted_value,
        )
        self.pending_terminal_event = {
            "outcome": outcome,
            "activity_status": activity_status,
            "interrupt": interrupt,
            "run_state": run_state or activity_status,
        }

    async def emit_prepared_terminal_event(self) -> None:
        if self.has_terminal_event or not self.pending_terminal_event:
            return
        await self._set_activity_status(self.pending_terminal_event["activity_status"])
        if self.run_input:
            await self._emit(
                RunFinishedEventExt(
                    type=EventType.RUN_FINISHED,
                    thread_id=self.run_input.thread_id,
                    run_id=self.run_id,
                    outcome=self.pending_terminal_event["outcome"],
                    interrupt=self.pending_terminal_event["interrupt"],
                    result=self.final_result.model_dump(mode="json") if self.final_result else None,
                )
            )
            await self._emit(
                StateDeltaEvent(
                    type=EventType.STATE_DELTA,
                    delta=[{"op": "replace", "path": "/runStatus", "value": self.pending_terminal_event["run_state"]}],
                )
            )
        self.has_terminal_event = True
        self.pending_terminal_event = None

    async def on_agent_finish(self, result: AgentResult):
        await self._prepare_terminal_event(
            result=result,
            outcome="success",
            activity_status="completed",
            run_state="completed",
        )

    async def on_agent_interrupt(self, result: AgentResult):
        for call in result.client_tool_calls or []:
            await self._finalize_activity_tool(call.tool_call_id, "awaiting_client")
        await self._prepare_terminal_event(
            result=result,
            outcome="interrupt",
            activity_status="interrupted",
            interrupt=AgUiInterrupt(
                id=AgentService.build_interrupt_id(self.run_id),
                reason="tool_result_required",
                payload=self._to_interrupt_payload(result.client_tool_calls or []),
            ),
            run_state="interrupted",
        )

    async def on_agent_cancel(self, result: AgentResult) -> None:
        await self._prepare_terminal_event(
            result=result,
            outcome="cancelled",
            activity_status="cancelled",
            run_state="cancelled",
        )

    async def on_agent_error(self, error: Exception):
        if self.has_terminal_event:
            return
        self.has_terminal_event = True
        self.pending_terminal_event = None
        await self._close_open_messages()
        await self._set_activity_status("error")
        if self.run_input:
            await self._emit(
                RunErrorEvent(
                    type=EventType.RUN_ERROR,
                    thread_id=self.run_input.thread_id,
                    run_id=self.run_id,
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
        self.workspace_dao = WorkspaceDao(context.db)
        self.redis = context.redis_service
        self.module_service = ServiceModuleService(context)
        self.ai_provider = AICapabilityProvider(context)
        self.agent_memory_var_service = AgentMemoryVarService(context)
        self.long_term_service = LongTermContextService(context)
        self.prompt_template = PromptTemplate()
        self.protocol_adapters = ProtocolAdapterRegistry()
        self.execution_ledger_service = ExecutionLedgerService(context)
        self.protocol_adapters.register("ag-ui", AgUiProtocolAdapter())
        self.resource_resolver = BaseResourceService(context)
        self._db_session_factory = context.db_session_factory or SessionLocal
        self._owned_runtime_session_cm = None
        self._owns_runtime_session = False

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

    async def _close_owned_runtime_session(self) -> None:
        if not self._owned_runtime_session_cm:
            return
        try:
            await self._owned_runtime_session_cm.__aexit__(None, None, None)
        finally:
            self._owned_runtime_session_cm = None
            self._owns_runtime_session = False

    async def _run_in_runtime_session(
        self,
        *,
        instance_uuid: str,
        run_input: RunAgentInputExt,
        actor: User,
        runtime_workspace: Optional[Workspace],
    ) -> AgentRunResult:
        session_cm = self._db_session_factory()
        runtime_db = await session_cm.__aenter__()
        runtime_context = self.context.model_copy(
            update={
                "db": runtime_db,
                "db_session_factory": self._db_session_factory,
            }
        )
        runtime_service = AgentService(runtime_context)
        runtime_service._owned_runtime_session_cm = session_cm
        runtime_service._owns_runtime_session = True
        try:
            return await runtime_service._async_execute_internal(
                instance_uuid=instance_uuid,
                run_input=run_input,
                actor=actor,
                runtime_workspace=runtime_workspace,
                skip_permission_check=True,
            )
        except Exception:
            await runtime_service._close_owned_runtime_session()
            raise

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
        if self._owns_runtime_session:
            return await self._async_execute_internal(
                instance_uuid=instance_uuid,
                run_input=run_input,
                actor=actor,
                runtime_workspace=runtime_workspace,
                skip_permission_check=True,
            )

        instance = await self.get_by_uuid(instance_uuid)
        if not instance:
            raise NotFoundError("Agent not found")
        await self._check_execute_perm(instance)
        return await self._run_in_runtime_session(
            instance_uuid=instance_uuid,
            run_input=run_input,
            actor=actor,
            runtime_workspace=runtime_workspace,
        )

    async def _async_execute_internal(
        self,
        *,
        instance_uuid: str,
        run_input: RunAgentInputExt,
        actor: User,
        runtime_workspace: Optional[Workspace] = None,
        skip_permission_check: bool = False,
    ) -> AgentRunResult:
        instance = await self.get_by_uuid(instance_uuid)
        if not instance:
            raise NotFoundError("Agent not found")
        if not skip_permission_check:
            await self._check_execute_perm(instance)

        workspace = await self._resolve_runtime_workspace(
            instance=instance,
            runtime_workspace=runtime_workspace,
        )

        try:
            agent_config = AgentConfig(**instance.agent_config)
        except Exception as exc:
            raise ConfigurationError(f"Agent {instance.uuid} config invalid: {exc}")

        protocol_name = self._resolve_protocol_name(run_input)
        adapter = self.protocol_adapters.get(protocol_name)
        if adapter is None:
            raise ServiceException(f"Unsupported protocol '{protocol_name}'.")

        tool_executor = ResourceAwareToolExecutor(self.context, workspace)
        adapted = adapter.adapt(run_input, tool_registrar=tool_executor)
        if not adapted.input_content and not adapted.custom_history and not adapted.resume_messages:
            raise ServiceException("Agent input content is required.")

        generator_manager = AsyncGeneratorManager()
        dependencies = await self.ref_dao.get_dependencies(instance.id)

        trace_id = str(uuid.uuid4())
        requested_thread_id = self._normalize_thread_id(adapted.thread_id)
        if not requested_thread_id:
            raise ServiceException("Agent threadId is required.")
        session_thread_id = self._normalize_uuid(requested_thread_id)
        session_mode = self._resolve_session_mode(run_input)
        requires_session_binding = self._requires_persistent_session_binding(
            run_input,
            requested_thread_id=requested_thread_id,
        )
        parent_run_id = self._normalize_parent_run_id(run_input.parent_run_id)
        resume_interrupt_id = self._normalize_interrupt_id(adapted.resume_interrupt_id)
        if resume_interrupt_id and not parent_run_id:
            parent_run_id = resume_interrupt_id

        parent_execution = None
        turn_id: Optional[str] = None
        if parent_run_id:
            parent_execution = await self.execution_ledger_service.resolve_parent_execution(
                parent_run_id=parent_run_id,
                instance=instance,
                actor=actor,
                thread_id=requested_thread_id,
            )
            if parent_execution is None:
                parent_run_id = None
            else:
                turn_id = await self.execution_ledger_service.resolve_lineage_root_run_id(
                    execution=parent_execution,
                    instance=instance,
                    actor=actor,
                    thread_id=requested_thread_id,
                )
                if not turn_id:
                    parent_execution = None
                    parent_run_id = None

        if resume_interrupt_id:
            if parent_execution is None:
                raise ServiceException("resume interruptId is invalid for the current session/thread.")
            if parent_execution.status != ResourceExecutionStatus.INTERRUPTED:
                raise ServiceException("resume interruptId must reference an interrupted run.")
            if resume_interrupt_id != parent_execution.run_id:
                raise ServiceException("resume interruptId does not match the parent run.")

        execution: Optional[ResourceExecution] = None
        try:
            execution = await self.execution_ledger_service.create_execution(
                instance=instance,
                actor=actor,
                thread_id=requested_thread_id,
                parent_run_id=parent_run_id,
            )
            await self.db.commit()

            turn_id = turn_id or execution.run_id
            canonical_run_input = run_input.model_copy(
                update={
                    "run_id": execution.run_id,
                    "thread_id": requested_thread_id,
                    "parent_run_id": parent_run_id,
                }
            )
            message_ids = self._build_stream_message_ids()

            session_manager: Optional[AgentSessionManager] = None
            if session_thread_id and session_mode != "stateless":
                candidate = AgentSessionManager(
                    self.context,
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
                await self.execution_ledger_service.mark_finished(
                    execution,
                    status=ResourceExecutionStatus.FAILED,
                    error_code="AGENT_SESSION_REQUIRED",
                    error_message=error_message,
                )
                await self.db.commit()
                raise ServiceException(error_message)

            trace_manager = TraceManager(
                db=self.db,
                operation_name="agent.run",
                user_id=actor.id,
                force_trace_id=trace_id,
                target_instance_id=instance.id,
                attributes=None
            )

            run_task = asyncio.create_task(
                self._run_agent_background_task(
                    agent_config=agent_config,
                    llm_module_version=instance.llm_module_version,
                    runtime_workspace=workspace,
                    trace_manager=trace_manager,
                    generator_manager=generator_manager,
                    execution=execution,
                    turn_id=turn_id,
                    session_manager=session_manager,
                    run_input=canonical_run_input,
                    message_ids=message_ids,
                    dependencies=dependencies,
                    adapted=adapted,
                    tool_executor=tool_executor,
                    agent_instance=instance,
                )
            )

            return AgentRunResult(
                generator=generator_manager,
                config=agent_config,
                run_id=execution.run_id,
                turn_id=turn_id,
                trace_id=trace_id,
                thread_id=requested_thread_id,
                cancel=lambda: (not run_task.done()) and run_task.cancel(),
            )
        except Exception as exc:
            await self.db.rollback()
            if execution is not None:
                await self.execution_ledger_service.mark_finished(
                    execution,
                    status=ResourceExecutionStatus.FAILED,
                    error_code="AGENT_RUN_INIT_ERROR",
                    error_message=str(exc),
                )
                await self.db.commit()
            raise

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
    ):

        callbacks: Optional[PersistingAgentCallbacks] = None
        usage_accumulator = UsageAccumulator()
        final_result: Optional[AgentResult] = None
        pending_post_commit_dispatch = False
        try:
            callbacks = PersistingAgentCallbacks(
                generator_manager=generator_manager,
                session_manager=session_manager,
                trace_id=trace_manager.force_trace_id,
                run_id=execution.run_id,
                turn_id=turn_id,
                usage_accumulator=usage_accumulator,
                run_input=run_input,
                message_ids=message_ids,
            )

            if adapted is None or tool_executor is None or agent_instance is None:
                raise ServiceException("Agent background task missing runtime prerequisites.")

            session = session_manager.session if session_manager and session_manager.session else None
            lock_ctx = self._session_lock(session.uuid) if session else nullcontext()

            async with lock_ctx:
                if session:
                    await self.db.refresh(session)
                    preload_turns = ShortContextProcessor.compute_fetch_limit(
                        total_turns=session.turn_count,
                        max_turns=agent_config.io_config.history_turns,
                    )
                    if session.turn_count > 0:
                        await session_manager.preload_recent_messages(max(1, preload_turns))
                    await self._enforce_pending_tool_results(
                        session_manager=session_manager,
                        resume_tool_call_ids=adapted.resume_tool_call_ids,
                    )
                    if adapted.resume_messages:
                        self._buffer_protocol_history_messages(
                            session_manager=session_manager,
                            history=adapted.resume_messages,
                        )

                prompt_variables = await self.agent_memory_var_service.get_runtime_object(
                    agent_instance.version_id,
                    self.context.actor.id,
                    session.uuid if session else None,
                )
                rendered_system_prompt = self.prompt_template.render(
                    agent_instance.system_prompt,
                    prompt_variables,
                )
                history_messages = [*adapted.custom_history, *adapted.resume_messages]
                user_message = LLMMessage(role="user", content=adapted.input_content)

                pipeline_manager = AgentPipelineManager(
                    system_message=LLMMessage(role="system", content=rendered_system_prompt),
                    user_message=user_message,
                    history=history_messages,
                    tool_executor=tool_executor,
                ).add_standard_processors(
                    app_context=self.context,
                    agent_config=agent_config,
                    dependencies=dependencies or [],
                    runtime_workspace=runtime_workspace,
                    session_manager=session_manager,
                    prompt_variables=prompt_variables,
                )

                final_messages = await pipeline_manager.build_context()
                final_tools = await pipeline_manager.build_skill()

                module_context = await self.module_service.get_runtime_context(
                    version_id=llm_module_version.id,
                    actor=self.context.actor,
                    workspace=runtime_workspace
                )
                model_context_window = self._resolve_model_context_window(module_context.version.attributes)

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
                    stream=True
                )

                if session:
                    session_manager.buffer_message(
                        role=AgentMessageRole.USER,
                        message_uuid=message_ids.user_message_id if message_ids else None,
                        text_content=user_message.content if isinstance(user_message.content, str) else None,
                        content_parts=user_message.content if isinstance(user_message.content, list) else None,
                    )

                await self.execution_ledger_service.mark_running(execution, trace_id=trace_manager.force_trace_id)

                async with trace_manager as root_span:
                    try:
                        agent_input = AgentInput(messages=final_messages)
                        root_span.attributes = AgentAttributes(
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

            await self.execution_ledger_service.mark_finished(
                execution,
                status=status,
            )
            await self.db.commit()
            if pending_post_commit_dispatch and session_manager:
                await session_manager.dispatch_post_commit_jobs()
            if callbacks:
                try:
                    await callbacks.emit_prepared_terminal_event()
                except Exception as exc:
                    logger.error("Failed to emit terminal event for run %s: %s", execution.run_id, exc, exc_info=True)

        except asyncio.CancelledError:
            logger.info(f"Agent task cancelled. TraceID: {trace_manager.force_trace_id}")
            if session_manager:
                session_manager.clear_post_commit_jobs()
            await self.db.rollback()
            await self.execution_ledger_service.mark_finished(
                execution,
                status=ResourceExecutionStatus.CANCELLED,
                error_code="AGENT_CANCELLED",
                error_message="Operation cancelled.",
            )
            await self.db.commit()
            if callbacks and callbacks.pending_terminal_event:
                try:
                    await callbacks.emit_prepared_terminal_event()
                except Exception as exc:
                    logger.error("Failed to emit cancel terminal event for run %s: %s", execution.run_id, exc, exc_info=True)
            raise 
        except Exception as e:
            logger.error(f"Agent task error: {e}", exc_info=True)
            if session_manager:
                session_manager.clear_post_commit_jobs()
            await self.db.rollback()
            if callbacks and not callbacks.has_terminal_event:
                await callbacks.on_agent_error(e)
            await self.execution_ledger_service.mark_finished(
                execution,
                status=ResourceExecutionStatus.FAILED,
                error_code="AGENT_EXECUTION_ERROR",
                error_message=str(e),
            )
            await self.db.commit()
        finally:
            await generator_manager.aclose(force=False)
            await self._close_owned_runtime_session()

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
