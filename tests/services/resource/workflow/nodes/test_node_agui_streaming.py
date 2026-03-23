import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.resource.agent import agent_service as agent_service_module
from app.engine.model.llm import LLMMessage, LLMResult, LLMUsage
from app.engine.schemas.parameter_schema import ParameterSchema
from app.engine.utils.stream import StreamBroadcaster
from app.engine.workflow.definitions import WorkflowNode
from app.services.common.llm_capability_provider import UsageAccumulator
from app.services.resource.workflow.nodes.node import AppLLMNode, BaseLLMNodeProcessor, WorkflowLLMCallbacks
from app.utils.async_generator import AsyncGeneratorManager


pytestmark = pytest.mark.asyncio


class _DummyProcessor(BaseLLMNodeProcessor):
    def __init__(self):
        self.context = SimpleNamespace(variables={})
        self.node = SimpleNamespace(id="workflow-node-1")


class _CollectBroadcaster:
    def __init__(self):
        self.items = []

    async def broadcast(self, payload):
        self.items.append(payload)


class _ModelEvent:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self, mode="json", by_alias=True, exclude_none=True):
        return self._payload


async def test_workflow_llm_callbacks_emit_light_ag_ui_events():
    generator = AsyncGeneratorManager()
    callbacks = WorkflowLLMCallbacks(generator_manager=generator, usage_accumulator=UsageAccumulator())

    await callbacks.on_chunk_generated("hello ")
    await callbacks.on_reasoning_chunk("thinking")
    await callbacks.on_success(
        LLMResult(
            message=LLMMessage(role="assistant", content="hello world"),
            usage=LLMUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            reasoning_content="thinking",
        )
    )

    e1 = await generator.get()
    e2 = await generator.get()
    e3 = await generator.get()
    assert e1["type"] == "TEXT_MESSAGE_CONTENT"
    assert e1["delta"] == "hello "
    assert e2["type"] == "REASONING_MESSAGE_CONTENT"
    assert e2["delta"] == "thinking"
    assert e3["type"] == "RUN_FINISHED"
    assert e3["result"]["message"]["content"] == "hello world"
    assert e3["result"]["reasoning_content"] == "thinking"


async def test_generate_text_stream_broadcasts_text_and_reasoning_and_persists_reasoning_output():
    processor = _DummyProcessor()
    generator = AsyncGeneratorManager()
    broadcaster = _CollectBroadcaster()
    outputs_schema = [ParameterSchema(name="text", type="string", label="text")]
    outputs_schema = processor._ensure_reasoning_output_schema(outputs_schema, include_reasoning=True)

    await generator.put(_ModelEvent({"type": "TEXT_MESSAGE_CONTENT", "delta": "Hello "}))
    await generator.put(_ModelEvent({"type": "REASONING_MESSAGE_CONTENT", "delta": "R1"}))
    await generator.put(_ModelEvent({"type": "TEXT_MESSAGE_CONTENT", "delta": "World"}))
    await generator.put(_ModelEvent({"type": "REASONING_MESSAGE_CONTENT", "delta": "R2"}))
    await generator.put(
        _ModelEvent(
            {
                "type": "RUN_FINISHED",
                "result": {
                    "message": {"content": "Hello World"},
                    "reasoning_content": "R1R2",
                },
            }
        )
    )
    await generator.aclose(force=False)

    output = await processor._generate_text_or_markdown(
        generator,
        outputs_schema,
        broadcaster,
        include_reasoning=True,
    )

    assert output["text"] == "Hello World"
    assert output["reasoning_content"] == "R1R2"
    assert broadcaster.items == [
        {"text": "Hello ", "reasoning_content": ""},
        {"text": "", "reasoning_content": "R1"},
        {"text": "World", "reasoning_content": ""},
        {"text": "", "reasoning_content": "R2"},
    ]


async def test_generate_json_stream_sets_reasoning_content_output():
    processor = _DummyProcessor()
    generator = AsyncGeneratorManager()
    outputs_schema = [ParameterSchema(name="result", type="string", label="result")]
    outputs_schema = processor._ensure_reasoning_output_schema(outputs_schema, include_reasoning=True)

    await generator.put({"type": "TEXT_MESSAGE_CONTENT", "delta": "{\"ok\": true}"})
    await generator.put({"type": "REASONING_MESSAGE_CONTENT", "delta": "reason"})
    await generator.put(
        {
            "type": "RUN_FINISHED",
            "result": {
                "message": {"content": "{\"ok\": true}"},
                "reasoning_content": "reason",
            },
        }
    )
    await generator.aclose(force=False)

    output = await processor._generate_json(generator, outputs_schema, include_reasoning=True)
    assert output["ok"] is True
    assert output["reasoning_content"] == "reason"


async def test_generate_text_falls_back_to_multimodal_final_message_content():
    processor = _DummyProcessor()
    generator = AsyncGeneratorManager()
    outputs_schema = [ParameterSchema(name="text", type="string", label="text")]

    await generator.put(
        {
            "type": "RUN_FINISHED",
            "result": {
                "message": {
                    "content": [
                        {"type": "text", "text": "Hello"},
                        {"type": "image", "image": "ignored"},
                        {"type": "text", "text": "World"},
                    ]
                }
            },
        }
    )
    await generator.aclose(force=False)

    output = await processor._generate_text_or_markdown(generator, outputs_schema)
    assert output["text"] == "Hello\nWorld"


async def test_app_llm_node_stream_broadcaster_emits_chunks_before_final_result(monkeypatch):
    class FakeAIProvider:
        async def resolve_model_version(self, module_uuid=None):
            return SimpleNamespace(id=1)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

        async def with_billing(self, **kwargs):
            return await kwargs["execution_func"]()

        async def execute_llm(self, *, callbacks, **kwargs):
            await callbacks.on_chunk_generated("A")
            await asyncio.sleep(0.01)
            await callbacks.on_chunk_generated("B")
            await asyncio.sleep(0.01)
            return LLMResult(
                message=LLMMessage(role="assistant", content="AB"),
                usage=LLMUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )

    class FakeAgentService:
        def __init__(self, context):
            self.ai_provider = FakeAIProvider()
            self.module_service = SimpleNamespace(
                get_runtime_context=AsyncMock(
                    return_value=SimpleNamespace(version=SimpleNamespace(name="gpt-test"))
                )
            )
            self.prompt_template = SimpleNamespace(render=lambda text, variables: text)

    monkeypatch.setattr(agent_service_module, "AgentService", FakeAgentService)

    node = WorkflowNode.model_validate(
        {
            "id": "llm",
            "data": {
                "registryId": "LLMNode",
                "name": "LLM",
                "inputs": [],
                "outputs": [{"name": "text", "type": "string"}],
                "config": {
                    "llm_module_version_uuid": "module-1",
                    "agent_config": {
                        "io_config": {"response_format": {"type": "text"}},
                    },
                    "system_prompt": "write",
                },
            },
        }
    )

    external_context = SimpleNamespace(
        app_context=SimpleNamespace(actor=SimpleNamespace(id=1)),
        runtime_workspace=SimpleNamespace(id=9),
    )
    context = SimpleNamespace(
        variables={},
        external_context=external_context,
    )

    executor = AppLLMNode(context, node, True)
    result = await executor.execute()

    assert isinstance(result.data, StreamBroadcaster)
    stream = result.data.subscribe()

    first_chunk = await asyncio.wait_for(anext(stream), timeout=0.2)
    second_chunk = await asyncio.wait_for(anext(stream), timeout=0.2)
    final_output = await asyncio.wait_for(result.data.get_result(), timeout=0.2)

    assert first_chunk == {"text": "A"}
    assert second_chunk == {"text": "B"}
    assert final_output["text"] == "AB"
