import pytest

from app.engine.workflow import WorkflowEngineService


pytestmark = pytest.mark.asyncio


def _build_minimal_object_flow():
    return {
        "nodes": [
            {
                "id": "start",
                "data": {
                    "registryId": "Start",
                    "name": "Start",
                    "outputs": [
                        {
                            "name": "message",
                            "type": "string",
                            "required": True,
                            "open": True,
                        }
                    ],
                },
            },
            {
                "id": "end",
                "data": {
                    "registryId": "End",
                    "name": "End",
                    "config": {"returnType": "Object", "stream": False},
                    "inputs": [
                        {
                            "name": "result",
                            "type": "string",
                            "required": True,
                            "open": True,
                            "value": {
                                "type": "ref",
                                "content": {"blockID": "start", "path": "message"},
                            },
                        }
                    ],
                },
            },
        ],
        "edges": [
            {
                "sourceNodeID": "start",
                "targetNodeID": "end",
                "sourcePortID": "0",
                "targetPortID": "0",
            }
        ],
    }


def _build_minimal_text_flow():
    return {
        "nodes": [
            {
                "id": "start",
                "data": {
                    "registryId": "Start",
                    "name": "Start",
                    "outputs": [
                        {
                            "name": "message",
                            "type": "string",
                            "required": True,
                            "open": True,
                        }
                    ],
                },
            },
            {
                "id": "end",
                "data": {
                    "registryId": "End",
                    "name": "End",
                    "config": {
                        "returnType": "Text",
                        "stream": False,
                        "content": "Workflow result: {{result}}",
                    },
                    "inputs": [
                        {
                            "name": "result",
                            "type": "string",
                            "required": True,
                            "open": True,
                            "value": {
                                "type": "ref",
                                "content": {"blockID": "start", "path": "message"},
                            },
                        }
                    ],
                },
            },
        ],
        "edges": [
            {
                "sourceNodeID": "start",
                "targetNodeID": "end",
                "sourcePortID": "0",
                "targetPortID": "0",
            }
        ],
    }


async def test_start_end_minimal_object_flow_maps_input_to_output():
    engine = WorkflowEngineService()

    result = await engine.run(
        _build_minimal_object_flow(),
        payload={"message": "hello workflow"},
    )

    assert result.output == {"result": "hello workflow"}
    assert result.content is None


async def test_start_end_minimal_text_flow_renders_template_from_start_input():
    engine = WorkflowEngineService()

    result = await engine.run(
        _build_minimal_text_flow(),
        payload={"message": "hello workflow"},
    )

    assert result.output == {"result": "hello workflow"}
    assert result.content == "Workflow result: hello workflow"
