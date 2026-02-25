import asyncio
import json
import logging
from contextlib import suppress
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from app.engine.model.llm import LLMEngineCallbacks, LLMMessage, LLMResult, LLMRunConfig, LLMToolCall, LLMUsage
from app.engine.utils.parameter_schema_utils import schemas2obj
from app.engine.utils.stream import StreamBroadcaster
from app.engine.workflow.definitions import NodeExecutionResult, NodeResultData, ParameterSchema
from app.engine.workflow.registry import BaseNode, register_node
from app.schemas.resource.execution_schemas import AnyExecutionRequest
from app.services.common.llm_capability_provider import UsageAccumulator
from app.services.exceptions import NotFoundError
from app.utils.async_generator import AsyncGeneratorManager

from .template import AGENT_TEMPLATE, LLM_TEMPLATE, TOOL_TEMPLATE

logger = logging.getLogger(__name__)


class WorkflowLLMCallbacks(LLMEngineCallbacks):
    """将 LLM 引擎回调适配为统一的 generator 事件。"""

    def __init__(self, generator_manager: AsyncGeneratorManager, usage_accumulator: UsageAccumulator):
        self.generator_manager = generator_manager
        self.usage_accumulator = usage_accumulator
        self.final_result: Optional[LLMResult] = None
        self.error_emitted: bool = False
        self._chunks: List[str] = []

    async def _emit(self, event: str, data: Optional[Dict[str, Any]] = None) -> None:
        await self.generator_manager.put(SimpleNamespace(event=event, data=data or {}))

    async def on_start(self) -> None:
        return None

    async def on_chunk_generated(self, chunk: str) -> None:
        self._chunks.append(chunk)
        await self._emit("chunk", {"content": chunk})

    async def on_tool_calls_generated(self, tool_calls: List[LLMToolCall]) -> None:
        # Workflow LLM 节点当前只关心文本输出；工具调用由 Agent 节点处理。
        return None

    async def on_success(self, result: LLMResult) -> None:
        self.final_result = result
        content = result.message.content or ""
        if not content and self._chunks:
            content = "".join(self._chunks)
        await self._emit("finish", {"content": content})

    async def on_error(self, error: Exception) -> None:
        self.error_emitted = True
        await self._emit("error", {"error": str(error)})

    async def on_usage(self, usage: LLMUsage) -> None:
        self.usage_accumulator.add(usage)

    async def on_cancel(self, result: LLMResult) -> None:
        self.final_result = result
        content = result.message.content or ""
        if not content and self._chunks:
            content = "".join(self._chunks)
        await self._emit("cancel", {"content": content})


class BaseLLMNodeProcessor:
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
    def _extract_content(event_data: Any) -> str:
        if not isinstance(event_data, dict):
            return ""
        content = event_data.get("content")
        if isinstance(content, str):
            return content
        message = event_data.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"]
        return ""

    async def _generate_text_or_markdown(
        self,
        generator: AsyncGeneratorManager,
        outputs_schema: List[ParameterSchema],
        broadcaster: Optional[StreamBroadcaster] = None
    ) -> Dict[str, Any]:
        primary_key = self._primary_output_key(outputs_schema)
        chunk_buffer: List[str] = []
        final_content = ""

        async for value in generator:
            event = getattr(value, "event", "")
            data = getattr(value, "data", {}) or {}

            if event == "chunk":
                chunk = self._extract_content(data)
                if chunk:
                    chunk_buffer.append(chunk)
                    if broadcaster:
                        await broadcaster.broadcast({primary_key: chunk})
                continue

            if event in {"finish", "cancel"}:
                final_content = self._extract_content(data) or "".join(chunk_buffer)
                continue

            if event == "error":
                raise RuntimeError(data.get("error") or "LLM/Agent generation failed.")

        if not final_content and chunk_buffer:
            final_content = "".join(chunk_buffer)

        base_output = await schemas2obj(outputs_schema, self.context.variables)
        base_output[primary_key] = final_content
        return base_output

    async def _generate_json(
        self,
        generator: AsyncGeneratorManager,
        outputs_schema: List[ParameterSchema],
        broadcaster: Optional[StreamBroadcaster] = None
    ) -> Dict[str, Any]:
        primary_key = self._primary_output_key(outputs_schema)
        chunk_buffer: List[str] = []
        final_content = ""

        async for value in generator:
            event = getattr(value, "event", "")
            data = getattr(value, "data", {}) or {}

            if event == "chunk":
                chunk = self._extract_content(data)
                if chunk:
                    chunk_buffer.append(chunk)
                    if broadcaster:
                        await broadcaster.broadcast({primary_key: chunk})
                continue

            if event in {"finish", "cancel"}:
                final_content = self._extract_content(data) or "".join(chunk_buffer)
                continue

            if event == "error":
                raise RuntimeError(data.get("error") or "LLM/Agent generation failed.")

        if not final_content and chunk_buffer:
            final_content = "".join(chunk_buffer)

        base_output = await schemas2obj(outputs_schema, self.context.variables)
        if not final_content:
            return base_output

        try:
            parsed = json.loads(final_content)
        except json.JSONDecodeError:
            logger.warning("JSON response parsing failed in node %s, fallback to raw text.", self.node.id)
            base_output[primary_key] = final_content
            return base_output

        if isinstance(parsed, dict):
            base_output.update(parsed)
        else:
            base_output[primary_key] = parsed
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
        outputs_schema = self.node.data.outputs or []
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
                    await generator.put(SimpleNamespace(event="error", data={"error": str(exc)}))
            finally:
                await generator.aclose(force=False)

        upstream_task = asyncio.create_task(run_llm_task())
        generator_func = self._generate_json if self._is_json_response(response_format) else self._generate_text_or_markdown

        async def consume_output(broadcaster: Optional[StreamBroadcaster] = None) -> Dict[str, Any]:
            try:
                return await generator_func(generator, outputs_schema, broadcaster)
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
        from app.services.resource.agent.agent_service import AgentExecutionInputs, AgentExecutionRequest, AgentService

        external_context = self.context.external_context
        app_context = external_context.app_context
        runtime_workspace = external_context.runtime_workspace

        node_input = await schemas2obj(self.node.data.inputs, self.context.variables)
        outputs_schema = self.node.data.outputs or []

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
        execute_params = AgentExecutionRequest(
            inputs=AgentExecutionInputs(
                input_query=input_query,
                session_uuid=session_uuid,
                history=history
            )
        )
        result = await agent_service.async_execute(
            instance_uuid=agent_instance_uuid,
            execute_params=execute_params,
            actor=app_context.actor,
            runtime_workspace=runtime_workspace
        )

        generator = result.generator
        response_format = getattr(result.config.io_config, "response_format", {"type": "text"})
        generator_func = self._generate_json if self._is_json_response(response_format) else self._generate_text_or_markdown

        if use_stream:
            broadcaster = StreamBroadcaster(self.node.id)
            broadcaster.create_task(generator_func(generator, outputs_schema, broadcaster))
            return NodeExecutionResult(input=node_input, data=broadcaster)

        output = await generator_func(generator, outputs_schema)
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
        request = AnyExecutionRequest(inputs=node_input)
        result = await exec_service.execute_instance(
            instance_uuid=tool_uuid,
            execute_params=request,
            actor=app_context.actor,
            runtime_workspace=runtime_workspace
        )

        if not result.success:
            raise RuntimeError(f"Tool execution failed: {result.error_message}")

        return NodeExecutionResult(input=node_input, data=NodeResultData(output=result.data))
