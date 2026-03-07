from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.engine.model.llm import LLMTool, LLMToolFunction
from app.services.resource.agent.processors import DependencySkillsProcessor


class _FakeToolExecutor:
    def __init__(self):
        self.registered = []

    def register_resource_instance(self, tool_def, instance_uuid):
        self.registered.append((tool_def, instance_uuid))


@pytest.mark.asyncio
async def test_dependency_skills_processor_uses_prebuilt_tool_schema_without_full_load():
    processor = object.__new__(DependencySkillsProcessor)
    processor.dependencies = [
        SimpleNamespace(
            alias="weather_lookup",
            target_resource=SimpleNamespace(name="Weather Tool"),
            target_instance=SimpleNamespace(
                uuid="instance-1",
                tool_schema={
                    "type": "function",
                    "function": {
                        "name": "call_weather_base",
                        "description": "Base weather lookup",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ),
        )
    ]
    processor.resource_service = SimpleNamespace(
        _get_full_instance_and_service=AsyncMock(),
    )
    executor = _FakeToolExecutor()

    await processor.process(executor)

    processor.resource_service._get_full_instance_and_service.assert_not_awaited()
    assert len(executor.registered) == 1
    tool_def, instance_uuid = executor.registered[0]
    assert instance_uuid == "instance-1"
    assert tool_def.function.name == "weather_lookup"
    assert processor.dependencies[0].target_instance.tool_schema["function"]["name"] == "call_weather_base"


@pytest.mark.asyncio
async def test_dependency_skills_processor_falls_back_when_prebuilt_tool_schema_missing():
    processor = object.__new__(DependencySkillsProcessor)
    tool_def = LLMTool(
        type="function",
        function=LLMToolFunction(
            name="call_weather_base",
            description="Base weather lookup",
            parameters={"type": "object", "properties": {}},
        ),
    )
    target_service = SimpleNamespace(as_llm_tool=AsyncMock(return_value=tool_def))
    processor.dependencies = [
        SimpleNamespace(
            alias=None,
            target_resource=SimpleNamespace(name="Weather Tool"),
            target_instance=SimpleNamespace(uuid="instance-1", tool_schema=None),
        )
    ]
    processor.resource_service = SimpleNamespace(
        _get_full_instance_and_service=AsyncMock(
            return_value=(SimpleNamespace(uuid="instance-1"), target_service)
        ),
    )
    executor = _FakeToolExecutor()

    await processor.process(executor)

    processor.resource_service._get_full_instance_and_service.assert_awaited_once_with("instance-1")
    target_service.as_llm_tool.assert_awaited_once()
    assert len(executor.registered) == 1
    registered_tool, instance_uuid = executor.registered[0]
    assert instance_uuid == "instance-1"
    assert registered_tool.function.name == "call_weather_base"
