import asyncio
import json
import logging
import uuid
from contextlib import suppress
from typing import Any, Dict, List, Optional, Tuple

from app.engine.model.llm import (
    LLMEngineCallbacks,
    LLMMessage,
    LLMResult,
    LLMRunConfig,
    LLMToolCall,
    LLMToolCallChunk,
    LLMUsage,
)
from app.engine.schemas.parameter_schema import ParameterSchema as EngineParameterSchema
from app.engine.utils.parameter_schema_utils import schemas2obj
from app.engine.utils.stream import StreamBroadcaster
from app.engine.workflow.definitions import NodeExecutionResult, NodeResultData, ParameterSchema
from app.engine.workflow.registry import BaseNode, register_node
from app.schemas.resource.execution_schemas import GenericExecutionRequest
from app.services.common.llm_capability_provider import UsageAccumulator
from app.services.exceptions import NotFoundError
from app.utils.async_generator import AsyncGeneratorManager

from .template import AGENT_TEMPLATE, LLM_TEMPLATE, TOOL_TEMPLATE

logger = logging.getLogger(__name__)


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: List[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
    return "\n".join(parts)


class WorkflowLLMCallbacks(LLMEngineCallbacks):
    """将 LLM 引擎回调适配为轻量 AG-UI 事件。"""

    def __init__(self, generator_manager: AsyncGeneratorManager, usage_accumulator: UsageAccumulator):
        self.generator_manager = generator_manager
        self.usage_accumulator = usage_accumulator
        self.final_result: Optional[LLMResult] = None
        self.error_emitted: bool = False
        self._chunks: List[str] = []
        self._reasoning_chunks: List[str] = []

    async def _emit(self, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        event: Dict[str, Any] = {"type": event_type}
        if payload:
            event.update(payload)
        await self.generator_manager.put(event)

    async def on_start(self) -> None:
        return None

    async def on_chunk_generated(self, chunk: str) -> None:
        self._chunks.append(chunk)
        await self._emit("TEXT_MESSAGE_CONTENT", {"delta": chunk})

    async def on_reasoning_chunk(self, chunk: str) -> None:
        self._reasoning_chunks.append(chunk)
        await self._emit("REASONING_MESSAGE_CONTENT", {"delta": chunk})

    async def on_tool_call_chunk(self, chunk: LLMToolCallChunk) -> None:
        # Workflow LLM 节点只关心最终文本/思考输出，不透传工具调用细节。
        return None

    async def on_tool_calls_generated(self, tool_calls: List[LLMToolCall]) -> None:
        # Workflow LLM 节点当前只关心文本输出；工具调用由 Agent 节点处理。
        return None

    async def on_success(self, result: LLMResult) -> None:
        self.final_result = result
        content = _content_to_text(result.message.content)
        if not content and self._chunks:
            content = "".join(self._chunks)
        reasoning_content = result.reasoning_content or "".join(self._reasoning_chunks)
        await self._emit(
            "RUN_FINISHED",
            {
                "result": {
                    "message": {"content": content},
                    "reasoning_content": reasoning_content,
                }
            },
        )

    async def on_error(self, error: Exception) -> None:
        self.error_emitted = True
        await self._emit("RUN_ERROR", {"message": str(error)})

    async def on_usage(self, usage: LLMUsage) -> None:
        self.usage_accumulator.add(usage)

    async def on_cancel(self, result: LLMResult) -> None:
        self.final_result = result
        content = _content_to_text(result.message.content)
        if not content and self._chunks:
            content = "".join(self._chunks)
        reasoning_content = result.reasoning_content or "".join(self._reasoning_chunks)
        await self._emit(
            "RUN_FINISHED",
            {
                "outcome": "cancelled",
                "result": {
                    "message": {"content": content},
                    "reasoning_content": reasoning_content,
                },
            },
        )


class BaseLLMNodeProcessor:
    REASONING_OUTPUT_NAME = "reasoning_content"

    @staticmethod
    def _primary_output_key(outputs_schema: List[ParameterSchema]) -> str:
        if not outputs_schema or not outputs_schema[0].name:
            raise NotFoundError("outputs_schema not found.")
        return outputs_schema[0].name

    @staticmethod
    def _response_type(response_format: Any) -> str:
        if isinstance(response_format, dict):
            return str(response_format.get("type", "text")).lower()
        if isinstance(response_format, str):
            return response_format.lower()
        return "text"

    def _is_json_response(self, response_format: Any) -> bool:
        return self._response_type(response_format) in {"json", "json_object", "json_schema"}

    @staticmethod
    def _ensure_reasoning_output_schema(
        outputs_schema: List[ParameterSchema],
        include_reasoning: bool,
    ) -> List[ParameterSchema]:
        if not include_reasoning:
            return list(outputs_schema or [])
        normalized = list(outputs_schema or [])
        if any(item.name == BaseLLMNodeProcessor.REASONING_OUTPUT_NAME for item in normalized):
            return normalized
        normalized.append(
            EngineParameterSchema(
                name=BaseLLMNodeProcessor.REASONING_OUTPUT_NAME,
                type="string",
                label="Reasoning Content",
                required=False,
            )
        )
        return normalized

    @staticmethod
    def _extract_content(event_data: Any) -> str:
        if not isinstance(event_data, dict):
            return ""
        content = event_data.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return _content_to_text(content)
        message = event_data.get("message")
        if isinstance(message, dict):
            return _content_to_text(message.get("content"))
        return ""

    @staticmethod
    def _normalize_event(value: Any) -> Tuple[str, Dict[str, Any]]:
        if hasattr(value, "model_dump"):
            data = value.model_dump(mode="json", by_alias=True, exclude_none=True)
            if isinstance(data, dict):
                return str(data.get("type", "")), data

        if isinstance(value, dict):
            if "type" in value:
                return str(value.get("type", "")), value
            event_name = str(value.get("event", ""))
            event_data = value.get("data")
            return event_name, event_data if isinstance(event_data, dict) else {}

        event_name = str(getattr(value, "event", ""))
        event_data = getattr(value, "data", {}) or {}
        if not isinstance(event_data, dict):
            event_data = {}
        return event_name, event_data

    def _extract_final_content(self, event_type: str, event_data: Dict[str, Any]) -> str:
        if event_type in {"RUN_FINISHED"}:
            result = event_data.get("result")
            if isinstance(result, dict):
                message = result.get("message")
                if isinstance(message, dict):
                    return _content_to_text(message.get("content"))
        if event_type in {"finish", "cancel"}:
            return self._extract_content(event_data)
        return ""

    @staticmethod
    def _extract_final_reasoning(event_type: str, event_data: Dict[str, Any]) -> str:
        if event_type != "RUN_FINISHED":
            return ""
        result = event_data.get("result")
        if not isinstance(result, dict):
            return ""
        reasoning_content = result.get("reasoning_content")
        if isinstance(reasoning_content, str):
            return reasoning_content
        message = result.get("message")
        if isinstance(message, dict) and isinstance(message.get("reasoning_content"), str):
            return message["reasoning_content"]
        return ""

    def _extract_error_message(self, event_type: str, event_data: Dict[str, Any]) -> Optional[str]:
        if event_type == "RUN_ERROR":
            message = event_data.get("message")
            if isinstance(message, str) and message:
                return message
            return "LLM/Agent generation failed."
        if event_type == "error":
            return event_data.get("error") or "LLM/Agent generation failed."
        return None

    async def _generate_text_or_markdown(
        self,
        generator: AsyncGeneratorManager,
        outputs_schema: List[ParameterSchema],
        broadcaster: Optional[StreamBroadcaster] = None,
        include_reasoning: bool = False,
    ) -> Dict[str, Any]:
        primary_key = self._primary_output_key(outputs_schema)
        chunk_buffer: List[str] = []
        reasoning_buffer: List[str] = []
        final_content = ""
        final_reasoning_content = ""

        async for value in generator:
            event_type, data = self._normalize_event(value)
            if not event_type:
                continue

            if event_type in {"TEXT_MESSAGE_CONTENT", "chunk"}:
                chunk = data.get("delta") if event_type == "TEXT_MESSAGE_CONTENT" else self._extract_content(data)
                if chunk:
                    chunk_buffer.append(chunk)
                    if broadcaster:
                        if include_reasoning:
                            await broadcaster.broadcast({primary_key: chunk, self.REASONING_OUTPUT_NAME: ""})
                        else:
                            await broadcaster.broadcast({primary_key: chunk})
                continue

            if event_type == "REASONING_MESSAGE_CONTENT":
                reasoning_chunk = data.get("delta")
                if isinstance(reasoning_chunk, str) and reasoning_chunk:
                    reasoning_buffer.append(reasoning_chunk)
                    if include_reasoning and broadcaster:
                        await broadcaster.broadcast({primary_key: "", self.REASONING_OUTPUT_NAME: reasoning_chunk})
                continue

            if event_type in {"RUN_FINISHED", "finish", "cancel"}:
                final_content = self._extract_final_content(event_type, data) or "".join(chunk_buffer)
                final_reasoning_content = self._extract_final_reasoning(event_type, data) or "".join(reasoning_buffer)
                continue

            error_msg = self._extract_error_message(event_type, data)
            if error_msg:
                raise RuntimeError(error_msg)

        if not final_content and chunk_buffer:
            final_content = "".join(chunk_buffer)
        if not final_reasoning_content and reasoning_buffer:
            final_reasoning_content = "".join(reasoning_buffer)

        base_output = await schemas2obj(outputs_schema, self.context.variables)
        base_output[primary_key] = final_content
        if include_reasoning:
            base_output[self.REASONING_OUTPUT_NAME] = final_reasoning_content
        return base_output

    async def _generate_json(
        self,
        generator: AsyncGeneratorManager,
        outputs_schema: List[ParameterSchema],
        broadcaster: Optional[StreamBroadcaster] = None,
        include_reasoning: bool = False,
    ) -> Dict[str, Any]:
        primary_key = self._primary_output_key(outputs_schema)
        chunk_buffer: List[str] = []
        reasoning_buffer: List[str] = []
        final_content = ""
        final_reasoning_content = ""

        async for value in generator:
            event_type, data = self._normalize_event(value)
            if not event_type:
                continue

            if event_type in {"TEXT_MESSAGE_CONTENT", "chunk"}:
                chunk = data.get("delta") if event_type == "TEXT_MESSAGE_CONTENT" else self._extract_content(data)
                if chunk:
                    chunk_buffer.append(chunk)
                    if broadcaster:
                        if include_reasoning:
                            await broadcaster.broadcast({primary_key: chunk, self.REASONING_OUTPUT_NAME: ""})
                        else:
                            await broadcaster.broadcast({primary_key: chunk})
                continue

            if event_type == "REASONING_MESSAGE_CONTENT":
                reasoning_chunk = data.get("delta")
                if isinstance(reasoning_chunk, str) and reasoning_chunk:
                    reasoning_buffer.append(reasoning_chunk)
                    if include_reasoning and broadcaster:
                        await broadcaster.broadcast({primary_key: "", self.REASONING_OUTPUT_NAME: reasoning_chunk})
                continue

            if event_type in {"RUN_FINISHED", "finish", "cancel"}:
                final_content = self._extract_final_content(event_type, data) or "".join(chunk_buffer)
                final_reasoning_content = self._extract_final_reasoning(event_type, data) or "".join(reasoning_buffer)
                continue

            error_msg = self._extract_error_message(event_type, data)
            if error_msg:
                raise RuntimeError(error_msg)

        if not final_content and chunk_buffer:
            final_content = "".join(chunk_buffer)
        if not final_reasoning_content and reasoning_buffer:
            final_reasoning_content = "".join(reasoning_buffer)

        base_output = await schemas2obj(outputs_schema, self.context.variables)
        if not final_content:
            if include_reasoning:
                base_output[self.REASONING_OUTPUT_NAME] = final_reasoning_content
            return base_output

        try:
            parsed = json.loads(final_content)
        except json.JSONDecodeError:
            logger.warning("JSON response parsing failed in node %s, fallback to raw text.", self.node.id)
            base_output[primary_key] = final_content
            if include_reasoning:
                base_output[self.REASONING_OUTPUT_NAME] = final_reasoning_content
            return base_output

        if isinstance(parsed, dict):
            base_output.update(parsed)
        else:
            base_output[primary_key] = parsed
        if include_reasoning:
            base_output[self.REASONING_OUTPUT_NAME] = final_reasoning_content
        return base_output


@register_node(template=LLM_TEMPLATE)
class AppLLMNode(BaseNode, BaseLLMNodeProcessor):
    async def execute(self) -> NodeExecutionResult:
        from app.services.resource.agent.agent_service import AgentService
        from app.schemas.resource.agent.agent_schemas import AgentConfig

        external_context = self.context.external_context
        app_context = external_context.app_context
        runtime_workspace = external_context.runtime_workspace

        node_input = await schemas2obj(self.node.data.inputs, self.context.variables)
        outputs_schema = list(self.node.data.outputs or [])
        config = self.node.data.config
        use_stream = self.is_stream_producer

        agent_service = AgentService(app_context)
        raw_agent_config = getattr(config, "agent_config", None)
        if not raw_agent_config:
            raise ValueError("LLM node config missing agent_config.")
        agent_config = (
            raw_agent_config
            if isinstance(raw_agent_config, AgentConfig)
            else AgentConfig.model_validate(raw_agent_config)
        )

        response_format = getattr(agent_config.io_config, "response_format", {"type": "text"})
        llm_module_uuid = getattr(config, "llm_module_version_uuid", None)
        system_prompt = getattr(config, "system_prompt", "") or ""
        history = getattr(config, "history", []) or []

        target_version = await agent_service.ai_provider.resolve_model_version(llm_module_uuid)
        module_context = await agent_service.module_service.get_runtime_context(
            version_id=target_version.id,
            actor=app_context.actor,
            workspace=runtime_workspace
        )

        messages: List[LLMMessage] = [LLMMessage(role="system", content=system_prompt)]
        for item in history:
            messages.append(item if isinstance(item, LLMMessage) else LLMMessage.model_validate(item))

        for message in messages:
            if isinstance(message.content, str) and message.content:
                message.content = agent_service.prompt_template.render(message.content, node_input)

        run_config = LLMRunConfig(
            model=module_context.version.name,
            temperature=agent_config.model_params.temperature,
            top_p=agent_config.model_params.top_p,
            presence_penalty=agent_config.model_params.presence_penalty,
            frequency_penalty=agent_config.model_params.frequency_penalty,
            max_tokens=agent_config.io_config.max_response_tokens,
            enable_thinking=agent_config.io_config.enable_deep_thinking,
            thinking_budget=agent_config.io_config.max_thinking_tokens,
            response_format=response_format,
            stream=use_stream
        )
        include_reasoning_output = bool(run_config.enable_thinking)
        outputs_schema = self._ensure_reasoning_output_schema(outputs_schema, include_reasoning_output)
        self.node.data.outputs = outputs_schema

        generator = AsyncGeneratorManager()
        usage_accumulator = UsageAccumulator()
        callbacks = WorkflowLLMCallbacks(generator_manager=generator, usage_accumulator=usage_accumulator)

        async def run_llm_task() -> None:
            try:
                estimated_input = len(str(messages)) // 3
                estimated_output = run_config.max_tokens

                async def _execute_llm():
                    return await agent_service.ai_provider.execute_llm(
                        module_context=module_context,
                        run_config=run_config,
                        messages=messages,
                        callbacks=callbacks
                    )

                await agent_service.ai_provider.with_billing(
                    runtime_workspace=runtime_workspace,
                    module_context=module_context,
                    estimated_input_tokens=estimated_input,
                    estimated_output_tokens=estimated_output,
                    usage_accumulator=usage_accumulator,
                    execution_func=_execute_llm
                )
            except Exception as exc:
                logger.error("Workflow LLM node failed: %s", exc, exc_info=True)
                if not callbacks.error_emitted:
                    await generator.put({"type": "RUN_ERROR", "message": str(exc)})
            finally:
                await generator.aclose(force=False)

        upstream_task = asyncio.create_task(run_llm_task())
        generator_func = self._generate_json if self._is_json_response(response_format) else self._generate_text_or_markdown

        async def consume_output(broadcaster: Optional[StreamBroadcaster] = None) -> Dict[str, Any]:
            try:
                return await generator_func(
                    generator,
                    outputs_schema,
                    broadcaster,
                    include_reasoning=include_reasoning_output,
                )
            finally:
                if not upstream_task.done():
                    upstream_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await upstream_task

        if use_stream:
            broadcaster = StreamBroadcaster(self.node.id)
            broadcaster.create_task(consume_output(broadcaster))
            return NodeExecutionResult(input=node_input, data=broadcaster)

        output = await consume_output()
        return NodeExecutionResult(input=node_input, data=NodeResultData(output=output))


@register_node(template=AGENT_TEMPLATE)
class AppAgentNode(BaseNode, BaseLLMNodeProcessor):
    async def execute(self) -> NodeExecutionResult:
        from app.schemas.protocol import RunAgentInputExt
        from app.services.resource.agent.agent_service import AgentService

        external_context = self.context.external_context
        app_context = external_context.app_context
        runtime_workspace = external_context.runtime_workspace

        node_input = await schemas2obj(self.node.data.inputs, self.context.variables)
        outputs_schema = list(self.node.data.outputs or [])

        config = self.node.data.config
        agent_instance_uuid = getattr(config, "resource_instance_uuid", None)
        if not agent_instance_uuid:
            raise ValueError("Agent instance UUID not configured.")

        enable_session = getattr(config, "enable_session", None)
        session_uuid = getattr(config, "session_uuid", None)
        if enable_session is True and not session_uuid:
            raise NotFoundError("Agent session UUID is required when session mode is enabled.")
        if enable_session is False:
            session_uuid = None

        history = None if session_uuid else getattr(config, "history", None)
        use_stream = self.is_stream_producer

        agent_service = AgentService(app_context)
        input_query = agent_service.prompt_template.render(getattr(config, "input_query", ""), node_input)
        input_content_parts = getattr(config, "input_content_parts", None)
        rendered_parts: Optional[List[Dict[str, Any]]] = None
        if isinstance(input_content_parts, list):
            rendered_parts = []
            for item in input_content_parts:
                if not isinstance(item, dict):
                    continue
                normalized_item = dict(item)
                if normalized_item.get("type") == "text" and isinstance(normalized_item.get("text"), str):
                    normalized_item["text"] = agent_service.prompt_template.render(normalized_item["text"], node_input)
                rendered_parts.append(normalized_item)

        thread_id = session_uuid or f"workflow-thread-{uuid.uuid4().hex[:16]}"
        messages: List[Dict[str, Any]] = []
        if history:
            for item in history:
                llm_message = item if isinstance(item, LLMMessage) else LLMMessage.model_validate(item)
                messages.append(
                    {
                        "id": f"hist-{uuid.uuid4().hex[:12]}",
                        "role": llm_message.role,
                        "content": llm_message.content,
                        "toolCalls": llm_message.tool_calls,
                        "toolCallId": llm_message.tool_call_id,
                    }
                )
        messages.append(
            {
                "id": f"user-{uuid.uuid4().hex[:12]}",
                "role": "user",
                "content": rendered_parts if rendered_parts else input_query,
            }
        )

        forwarded_props: Dict[str, Any] = {}
        if session_uuid:
            forwarded_props["sessionUuid"] = session_uuid
        if enable_session is False or (enable_session is not True and not session_uuid):
            forwarded_props["sessionMode"] = "stateless"

        run_input = RunAgentInputExt.model_validate(
            {
                "threadId": thread_id,
                "runId": f"workflow-run-{uuid.uuid4().hex[:16]}",
                "state": {"source": "workflow-node", "nodeId": self.node.id},
                "messages": messages,
                "tools": [],
                "context": [],
                "forwardedProps": forwarded_props,
            }
        )

        result = await agent_service.async_execute(
            instance_uuid=agent_instance_uuid,
            run_input=run_input,
            actor=app_context.actor,
            runtime_workspace=runtime_workspace
        )

        generator = result.generator
        response_format = getattr(result.config.io_config, "response_format", {"type": "text"})
        include_reasoning_output = bool(getattr(result.config.io_config, "enable_deep_thinking", False))
        outputs_schema = self._ensure_reasoning_output_schema(outputs_schema, include_reasoning_output)
        self.node.data.outputs = outputs_schema
        generator_func = self._generate_json if self._is_json_response(response_format) else self._generate_text_or_markdown

        if use_stream:
            broadcaster = StreamBroadcaster(self.node.id)
            broadcaster.create_task(
                generator_func(
                    generator,
                    outputs_schema,
                    broadcaster,
                    include_reasoning=include_reasoning_output,
                )
            )
            return NodeExecutionResult(input=node_input, data=broadcaster)

        output = await generator_func(
            generator,
            outputs_schema,
            include_reasoning=include_reasoning_output,
        )
        return NodeExecutionResult(input=node_input, data=NodeResultData(output=output))


@register_node(template=TOOL_TEMPLATE)
class AppToolNode(BaseNode):
    async def execute(self) -> NodeExecutionResult:
        from app.services.resource.execution.execution_service import ExecutionService

        external_context = self.context.external_context
        app_context = external_context.app_context
        runtime_workspace = external_context.runtime_workspace

        node_input = await schemas2obj(self.node.data.inputs, self.context.variables)
        tool_uuid = getattr(self.node.data.config, "resource_instance_uuid", None)
        if not tool_uuid:
            raise ValueError("Tool UUID not configured.")

        exec_service = ExecutionService(app_context)
        request = GenericExecutionRequest(inputs=node_input)
        result = await exec_service.execute_instance(
            instance_uuid=tool_uuid,
            execute_params=request,
            actor=app_context.actor,
            runtime_workspace=runtime_workspace
        )

        if not result.success:
            raise RuntimeError(f"Tool execution failed: {result.error_message}")

        return NodeExecutionResult(input=node_input, data=NodeResultData(output=result.data))
