import time

import pytest
from pydantic import ConfigDict, Field

from app.engine.schemas.parameter_schema import ParameterSchema
from app.engine.utils.parameter_schema_utils import schemas2obj
from app.engine.workflow import (
    BaseNodeConfig,
    NodeCategory,
    NodeData,
    NodeExecutionResult,
    NodeResultData,
    NodeTemplate,
    WorkflowEngineService,
    register_node,
)
from app.engine.workflow.registry import BaseNode


pytestmark = pytest.mark.asyncio


class DelayNodeConfig(BaseNodeConfig):
    delayMs: int = Field(default=250)
    model_config = ConfigDict(extra="forbid")


DELAY_TEMPLATE = NodeTemplate(
    category=NodeCategory.CUSTOM,
    icon="clock",
    data=NodeData(
        registryId="DelayNode",
        name="Delay Node",
        description="Sleeps then returns the loop item.",
        inputs=[ParameterSchema(name="item", type="string")],
        outputs=[ParameterSchema(name="done", type="string")],
        config=DelayNodeConfig(),
    ),
    forms=[],
)


@register_node(template=DELAY_TEMPLATE)
class DelayNode(BaseNode):
    async def execute(self) -> NodeExecutionResult:
        import asyncio

        node_input = await schemas2obj(self.node.data.inputs, self.context.variables)
        loop_ctx = self.context.variables.get("loop", {}) if isinstance(self.context.variables, dict) else {}
        item = loop_ctx.get("item", node_input.get("item", ""))
        await asyncio.sleep((self.node.data.config.delayMs or 250) / 1000.0)
        return NodeExecutionResult(
            input=node_input,
            data=NodeResultData(output={"done": item}),
        )


def _build_loop_workflow(execution_mode: str, max_concurrency: int):
    return {
        "nodes": [
            {
                "id": "start",
                "data": {
                    "registryId": "Start",
                    "name": "Start",
                    "inputs": [],
                    "outputs": [],
                    "config": {},
                },
            },
            {
                "id": "loop",
                "data": {
                    "registryId": "Loop",
                    "name": "Loop",
                    "inputs": [],
                    "outputs": [
                        {
                            "name": "results",
                            "type": "array",
                            "items": {"type": "string"},
                            "value": {
                                "type": "ref",
                                "content": {
                                    "source": "loop-block-output",
                                    "blockID": "delay",
                                    "path": "done",
                                },
                            },
                        }
                    ],
                    "config": {
                        "loopType": "list",
                        "executionMode": execution_mode,
                        "maxConcurrency": max_concurrency,
                        "loopList": {
                            "name": "loopList",
                            "type": "array",
                            "items": {"type": "string"},
                            "value": {"type": "literal", "content": ["a", "b", "c", "d"]},
                        },
                    },
                    "blocks": [
                        {
                            "id": "delay",
                            "data": {
                                "registryId": "DelayNode",
                                "name": "Delay",
                                "inputs": [
                                    {
                                        "name": "item",
                                        "type": "string",
                                        "value": {
                                            "type": "ref",
                                            "content": {"blockID": "loop", "path": "item"},
                                        },
                                    }
                                ],
                                "outputs": [{"name": "done", "type": "string"}],
                                "config": {"delayMs": 250},
                            },
                        }
                    ],
                    "edges": [
                        {
                            "sourceNodeID": "loop",
                            "targetNodeID": "delay",
                            "sourcePortID": "loop-function-inline-output",
                            "targetPortID": "0",
                        },
                        {
                            "sourceNodeID": "delay",
                            "targetNodeID": "loop",
                            "sourcePortID": "0",
                            "targetPortID": "loop-function-inline-input",
                        },
                    ],
                },
            },
            {
                "id": "end",
                "data": {
                    "registryId": "End",
                    "name": "End",
                    "inputs": [
                        {
                            "name": "results",
                            "type": "array",
                            "items": {"type": "string"},
                            "value": {
                                "type": "ref",
                                "content": {"blockID": "loop", "path": "results"},
                            },
                        }
                    ],
                    "outputs": [],
                    "config": {"returnType": "Object"},
                },
            },
        ],
        "edges": [
            {"sourceNodeID": "start", "targetNodeID": "loop", "sourcePortID": "0", "targetPortID": "0"},
            {"sourceNodeID": "loop", "targetNodeID": "end", "sourcePortID": "0", "targetPortID": "0"},
        ],
    }


async def test_loop_parallel_batch_runs_faster_and_preserves_order():
    engine = WorkflowEngineService()

    serial_started = time.perf_counter()
    serial_result = await engine.run(_build_loop_workflow("serial", 1), payload={})
    serial_elapsed = time.perf_counter() - serial_started

    parallel_started = time.perf_counter()
    parallel_result = await engine.run(_build_loop_workflow("parallel", 4), payload={})
    parallel_elapsed = time.perf_counter() - parallel_started

    assert serial_result.output["results"] == ["a", "b", "c", "d"]
    assert parallel_result.output["results"] == ["a", "b", "c", "d"]
    assert parallel_elapsed < serial_elapsed * 0.75
