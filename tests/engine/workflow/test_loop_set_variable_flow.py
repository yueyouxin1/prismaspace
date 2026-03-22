import pytest

from app.engine.workflow.definitions import (
    BaseNodeConfig,
    NodeCategory,
    NodeData,
    NodeExecutionResult,
    NodeResultData,
    NodeTemplate,
    ParameterSchema,
)
from app.engine.workflow.main import WorkflowEngineService
from app.engine.workflow.registry import BaseNode, register_node
from app.engine.utils.parameter_schema_utils import schemas2obj


pytestmark = pytest.mark.asyncio


class _LoopEchoConfig(BaseNodeConfig):
    pass


LOOP_ECHO_TEMPLATE = NodeTemplate(
    category=NodeCategory.CUSTOM,
    icon="zap",
    data=NodeData(
        registryId="LoopEchoNode",
        name="Loop Echo",
        description="Accumulate loop items into next summary.",
        inputs=[
            ParameterSchema(name="input", type="string", required=False, open=True),
            ParameterSchema(name="summary", type="string", required=False, open=True),
        ],
        outputs=[
            ParameterSchema(name="next_summary", type="string", required=False, open=True),
        ],
        config=_LoopEchoConfig(),
    ),
)


@register_node(template=LOOP_ECHO_TEMPLATE)
class LoopEchoNode(BaseNode):
    async def execute(self) -> NodeExecutionResult:
        node_input = await schemas2obj(self.node.data.inputs, self.context.variables)
        summary = str(node_input.get("summary") or "")
        item = str(node_input.get("input") or "")
        value = f"{summary}|{item}" if summary else item
        return NodeExecutionResult(
            input=node_input,
            data=NodeResultData(output={"next_summary": value}),
        )


async def test_loop_set_variable_persists_middle_variable_across_iterations():
    workflow = {
        "nodes": [
            {
                "id": "start",
                "data": {
                    "registryId": "Start",
                    "name": "Start",
                    "outputs": [
                        {
                            "name": "items",
                            "type": "array",
                            "items": {"type": "string"},
                            "required": True,
                            "open": True,
                        }
                    ],
                    "inputs": [],
                    "config": {},
                },
            },
            {
                "id": "loop",
                "data": {
                    "registryId": "Loop",
                    "name": "Loop",
                    "inputs": [
                        {
                            "name": "summary",
                            "type": "string",
                            "required": False,
                            "open": True,
                            "value": {"type": "literal", "content": ""},
                        }
                    ],
                    "outputs": [
                        {
                            "name": "final_summary",
                            "type": "string",
                            "required": False,
                            "open": True,
                            "value": {
                                "type": "ref",
                                "content": {"blockID": "loop", "path": "summary"},
                            },
                        }
                    ],
                    "config": {
                        "loopType": "list",
                        "executionMode": "serial",
                        "loopList": {
                            "name": "input",
                            "type": "array",
                            "items": {"type": "string"},
                            "required": True,
                            "open": True,
                            "value": {
                                "type": "ref",
                                "content": {"blockID": "start", "path": "items"},
                            },
                        },
                    },
                    "blocks": [
                        {
                            "id": "echo",
                            "data": {
                                "registryId": "LoopEchoNode",
                                "name": "Echo",
                                "inputs": [
                                    {
                                        "name": "input",
                                        "type": "string",
                                        "required": False,
                                        "open": True,
                                        "value": {
                                            "type": "ref",
                                            "content": {"blockID": "loop", "path": "input"},
                                        },
                                    },
                                    {
                                        "name": "summary",
                                        "type": "string",
                                        "required": False,
                                        "open": True,
                                        "value": {
                                            "type": "ref",
                                            "content": {"blockID": "loop", "path": "summary"},
                                        },
                                    },
                                ],
                                "outputs": [
                                    {
                                        "name": "next_summary",
                                        "type": "string",
                                        "required": False,
                                        "open": True,
                                    }
                                ],
                                "config": {},
                            },
                        },
                        {
                            "id": "set",
                            "data": {
                                "registryId": "SetVariable",
                                "name": "Set",
                                "inputs": [],
                                "outputs": [],
                                "config": {
                                    "assignments": [
                                        {
                                            "left": {
                                                "name": "left_1",
                                                "type": "string",
                                                "required": False,
                                                "open": True,
                                                "value": {
                                                    "type": "ref",
                                                    "content": {"blockID": "loop", "path": "summary"},
                                                },
                                            },
                                            "right": {
                                                "name": "right_1",
                                                "type": "string",
                                                "required": False,
                                                "open": True,
                                                "value": {
                                                    "type": "ref",
                                                    "content": {"blockID": "echo", "path": "next_summary"},
                                                },
                                            },
                                        }
                                    ]
                                },
                            },
                        },
                    ],
                    "edges": [
                        {
                            "sourceNodeID": "loop",
                            "targetNodeID": "echo",
                            "sourcePortID": "loop-function-inline-output",
                            "targetPortID": "0",
                        },
                        {
                            "sourceNodeID": "echo",
                            "targetNodeID": "set",
                            "sourcePortID": "0",
                            "targetPortID": "0",
                        },
                        {
                            "sourceNodeID": "set",
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
                            "name": "result",
                            "type": "string",
                            "required": False,
                            "open": True,
                            "value": {
                                "type": "ref",
                                "content": {"blockID": "loop", "path": "final_summary"},
                            },
                        }
                    ],
                    "outputs": [],
                    "config": {"returnType": "Object"},
                },
            },
        ],
        "edges": [
            {
                "sourceNodeID": "start",
                "targetNodeID": "loop",
                "sourcePortID": "0",
                "targetPortID": "0",
            },
            {
                "sourceNodeID": "loop",
                "targetNodeID": "end",
                "sourcePortID": "0",
                "targetPortID": "0",
            },
        ],
    }

    engine = WorkflowEngineService()
    result = await engine.run(workflow, payload={"items": ["A", "B", "C"]})

    assert result.output == {"result": "A|B|C"}