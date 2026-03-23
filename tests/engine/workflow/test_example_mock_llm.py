import pytest

from app.engine.workflow.example import mocks as _example_mocks
from app.engine.workflow.main import WorkflowEngineService


pytestmark = pytest.mark.asyncio


async def test_mock_llm_uses_runtime_input_for_markdown_output():
    workflow = {
        "nodes": [
            {
                "id": "start",
                "data": {
                    "registryId": "Start",
                    "name": "Start",
                    "outputs": [{"name": "topic", "type": "string"}],
                },
            },
            {
                "id": "llm",
                "data": {
                    "registryId": "MockLLM",
                    "name": "Mock LLM",
                    "config": {
                        "model": "gpt-4",
                        "response_format": "markdown",
                    },
                    "inputs": [
                        {
                            "name": "input_query",
                            "type": "string",
                            "value": {
                                "type": "ref",
                                "content": {"blockID": "start", "path": "topic"},
                            },
                        }
                    ],
                    "outputs": [{"name": "report_md", "type": "string"}],
                },
            },
            {
                "id": "end",
                "data": {
                    "registryId": "End",
                    "name": "End",
                    "inputs": [
                        {
                            "name": "report",
                            "type": "string",
                            "value": {
                                "type": "ref",
                                "content": {"blockID": "llm", "path": "report_md"},
                            },
                        }
                    ],
                    "config": {"returnType": "Object"},
                },
            },
        ],
        "edges": [
            {
                "sourceNodeID": "start",
                "targetNodeID": "llm",
                "sourcePortID": "0",
                "targetPortID": "0",
            },
            {
                "sourceNodeID": "llm",
                "targetNodeID": "end",
                "sourcePortID": "0",
                "targetPortID": "0",
            },
        ],
    }

    result = await WorkflowEngineService().run(workflow, payload={"topic": "abc"})

    assert result.output == {"report": "## Analysis\n**Echo:** cba"}


async def test_loop_aggregates_structured_mock_llm_output():
    workflow = {
        "nodes": [
            {
                "id": "start",
                "data": {
                    "registryId": "Start",
                    "name": "Start",
                    "outputs": [
                        {
                            "name": "products",
                            "type": "array",
                            "items": {"type": "string"},
                        }
                    ],
                },
            },
            {
                "id": "loop",
                "data": {
                    "registryId": "Loop",
                    "name": "Loop",
                    "config": {
                        "loopType": "list",
                        "loopList": {
                            "name": "list_ref",
                            "type": "array",
                            "items": {"type": "string"},
                            "value": {
                                "type": "ref",
                                "content": {"blockID": "start", "path": "products"},
                            },
                        },
                    },
                    "outputs": [
                        {
                            "name": "batch_results",
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": [
                                    {"name": "product_name", "type": "string"},
                                    {"name": "score", "type": "string"},
                                ],
                            },
                            "value": {
                                "type": "ref",
                                "content": {
                                    "source": "loop-block-output",
                                    "blockID": "inner_llm",
                                    "path": "analysis_json",
                                },
                            },
                        }
                    ],
                    "blocks": [
                        {
                            "id": "worker",
                            "data": {
                                "registryId": "UnstableWorker",
                                "name": "Worker",
                                "config": {
                                    "executionPolicy": {
                                        "switch": True,
                                        "retryTimes": 1,
                                        "processType": 2,
                                        "dataOnErr": "fallback_val",
                                    }
                                },
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
                                "outputs": [{"name": "processed_item", "type": "string"}],
                            },
                        },
                        {
                            "id": "inner_llm",
                            "data": {
                                "registryId": "MockLLM",
                                "name": "Inner LLM",
                                "config": {
                                    "model": "gpt-4",
                                    "response_format": "json",
                                },
                                "inputs": [
                                    {
                                        "name": "input_query",
                                        "type": "string",
                                        "value": {
                                            "type": "ref",
                                            "content": {
                                                "blockID": "worker",
                                                "path": "processed_item",
                                            },
                                        },
                                    }
                                ],
                                "outputs": [
                                    {
                                        "name": "analysis_json",
                                        "type": "object",
                                        "properties": [
                                            {"name": "product_name", "type": "string"},
                                            {"name": "score", "type": "string"},
                                        ],
                                    }
                                ],
                            },
                        },
                    ],
                    "edges": [
                        {
                            "sourceNodeID": "loop",
                            "targetNodeID": "worker",
                            "sourcePortID": "loop-function-inline-output",
                            "targetPortID": "0",
                        },
                        {
                            "sourceNodeID": "worker",
                            "targetNodeID": "inner_llm",
                            "sourcePortID": "0",
                            "targetPortID": "0",
                        },
                        {
                            "sourceNodeID": "inner_llm",
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
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": [
                                    {"name": "product_name", "type": "string"},
                                    {"name": "score", "type": "string"},
                                ],
                            },
                            "value": {
                                "type": "ref",
                                "content": {"blockID": "loop", "path": "batch_results"},
                            },
                        }
                    ],
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

    result = await WorkflowEngineService().run(
        workflow,
        payload={"products": ["MacBook Pro", "Buggy Product", "iPhone 15"]},
    )

    assert result.output["result"][0]["product_name"] == "MacBook Pro"
    assert result.output["result"][0]["score"] != ""
    assert result.output["result"][1]["product_name"] == ""
    assert result.output["result"][2]["product_name"] == "iPhone 15"
