import json
import time
from typing import Any, Callable, Dict, List, Optional, Set

from ag_ui.core import (
    ActivityDeltaEvent,
    ActivitySnapshotEvent,
    CustomEvent,
    EventType,
    MessagesSnapshotEvent,
    ReasoningEncryptedValueEvent,
    ReasoningEndEvent,
    ReasoningMessageContentEvent,
    ReasoningMessageEndEvent,
    ReasoningMessageStartEvent,
    ReasoningStartEvent,
    RunErrorEvent,
    RunStartedEvent,
    StateDeltaEvent,
    StateSnapshotEvent,
    StepFinishedEvent,
    StepStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)

from app.engine.agent import AgentClientToolCall, AgentEngineCallbacks, AgentResult, AgentStep
from app.engine.model.llm import LLMToolCall, LLMToolCallChunk, LLMUsage
from app.models.resource.agent import AgentMessageRole
from app.schemas.protocol import (
    AgUiInterrupt,
    AgUiInterruptPayload,
    AgUiInterruptToolCall,
    RunFinishedEventExt,
    RunAgentInputExt,
)
from app.services.common.llm_capability_provider import UsageAccumulator
from app.services.resource.agent.agent_session_manager import AgentSessionManager
from app.services.resource.agent.types.agent import AgentStreamMessageIds
from app.utils.async_generator import AsyncGeneratorManager
from app.utils.id_generator import generate_uuid


class PersistingAgentCallbacks(AgentEngineCallbacks):
    """
    负责事件推送、状态追踪和运行期消息持久化。
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
        interrupt_id_builder: Optional[Callable[[str], str]] = None,
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
        self.interrupt_id_builder = interrupt_id_builder or (lambda value: value)
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
        await self._emit(CustomEvent(type=EventType.CUSTOM, name=name, value=value))

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
        await self._emit_activity_delta([{"op": "replace", "path": "/status", "value": status}])

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
        await self._emit(ToolCallEndEvent(type=EventType.TOOL_CALL_END, tool_call_id=tool_call_id))
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
        await self._emit(MessagesSnapshotEvent(type=EventType.MESSAGES_SNAPSHOT, messages=self.messages_snapshot))
        await self._emit(StateSnapshotEvent(type=EventType.STATE_SNAPSHOT, snapshot=self.run_input.state))
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
        await self._emit(StepStartedEvent(type=EventType.STEP_STARTED, step_name=step_name))
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
        await self._emit(StepFinishedEvent(type=EventType.STEP_FINISHED, step_name=step_name))
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
            await self._emit(ReasoningStartEvent(type=EventType.REASONING_START, message_id=self.reasoning_message_id))
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
                id=self.interrupt_id_builder(self.run_id),
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
