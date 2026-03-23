import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.engine.utils.stream import StreamBroadcaster
from app.engine.workflow.context import WorkflowContext
from app.engine.workflow.nodes.control import EndNode
from app.engine.workflow.orchestrator import WorkflowOrchestrator
from app.engine.workflow.definitions import WorkflowNode
from app.engine.schemas.parameter_schema import ParameterSchema
from app.engine.utils.parameter_schema_utils import schemas2obj
from app.services.resource.workflow.runtime_persistence import WorkflowRuntimePersistenceService


pytestmark = pytest.mark.asyncio


async def test_wait_for_stream_keeps_broadcaster_in_context_variables():
    orchestrator = object.__new__(WorkflowOrchestrator)
    orchestrator.context_mgr = WorkflowContext({})
    orchestrator.context_mgr.init_node_state("llm")
    orchestrator.send = AsyncMock()
    orchestrator._queue_successors = AsyncMock()
    orchestrator.runtime_observer = None

    broadcaster = StreamBroadcaster("llm")

    async def produce():
        await broadcaster.broadcast({"report": "A"})
        await broadcaster.broadcast({"report": "B"})
        return {"report": "AB"}

    broadcaster.create_task(produce())
    orchestrator.context_mgr.set_variable("llm", broadcaster)

    await orchestrator._wait_for_stream(
        node_id="llm",
        node_spec=SimpleNamespace(),
        broadcaster=broadcaster,
        timeout_sec=1,
        started_at=time.time(),
    )

    assert orchestrator.context_mgr.variables["llm"] is broadcaster
    assert orchestrator.context_mgr.get_node_state("llm").result.output == {"report": "AB"}


async def test_runtime_persistence_jsonable_resolves_completed_broadcaster():
    broadcaster = StreamBroadcaster("llm")

    async def produce():
        await broadcaster.broadcast({"report": "final"})
        return {"report": "final"}

    broadcaster.create_task(produce())
    await broadcaster.get_result()

    serialized = WorkflowRuntimePersistenceService._jsonable({"llm": broadcaster})

    assert serialized == {"llm": {"report": "final"}}


async def test_end_node_stream_handler_replays_completed_broadcaster_history():
    broadcaster = StreamBroadcaster("llm")

    async def produce():
        await broadcaster.broadcast({"report": "A"})
        await broadcaster.broadcast({"report": "B"})
        return {"report": "AB"}

    broadcaster.create_task(produce())
    await broadcaster.get_result()

    node = WorkflowNode.model_validate(
        {
            "id": "end",
            "data": {
                "registryId": "End",
                "name": "End",
                "inputs": [],
                "outputs": [],
                "config": {
                    "stream": True,
                    "returnType": "Text",
                    "content": "{{result}}",
                },
            },
        }
    )

    context = SimpleNamespace(
        variables={"llm": broadcaster},
        version=0,
        get_ref_details=lambda consumer_node_id, variable_path: SimpleNamespace(blockID="llm", path="report"),
    )

    end_node = EndNode(context, node, False)
    chunks = []
    async for chunk in end_node._stream_content_handler("{{result}}"):
        chunks.append(chunk)

    assert chunks == ["A", "B"]


async def test_end_node_static_template_value_does_not_block_stream_variable():
    broadcaster = StreamBroadcaster("llm")
    allow_stream = asyncio.Event()
    finish_stream = asyncio.Event()

    async def produce():
        await allow_stream.wait()
        await broadcaster.broadcast({"report": "A"})
        await finish_stream.wait()
        return {"report": "A"}

    broadcaster.create_task(produce())

    node = WorkflowNode.model_validate(
        {
            "id": "end",
            "data": {
                "registryId": "End",
                "name": "End",
                "inputs": [
                    {
                        "name": "result",
                        "type": "object",
                        "properties": [
                            {"name": "param_4", "type": "string"},
                        ],
                        "value": {
                            "type": "ref",
                            "content": {"blockID": "start", "path": "result"},
                        },
                    },
                    {
                        "name": "llm",
                        "type": "string",
                        "value": {
                            "type": "ref",
                            "content": {"blockID": "llm", "path": "report"},
                        },
                    },
                ],
                "outputs": [],
                "config": {
                    "stream": True,
                    "returnType": "Text",
                    "content": "{{result.param_4}}{{llm}}",
                },
            },
        }
    )

    def _get_ref_details(consumer_node_id, variable_path):
        top_level = variable_path.split(".")[0]
        if top_level == "result":
            return SimpleNamespace(blockID="start", path="result")
        if top_level == "llm":
            return SimpleNamespace(blockID="llm", path="report")
        return None

    context = SimpleNamespace(
        variables={
            "start": {"result": {"param_4": "STATIC"}},
            "llm": broadcaster,
        },
        version=0,
        get_ref_details=_get_ref_details,
    )

    end_node = EndNode(context, node, False)
    stream_iter = end_node._stream_content_handler("{{result.param_4}}{{llm}}")

    first_chunk = await asyncio.wait_for(stream_iter.__anext__(), timeout=0.1)
    assert first_chunk == "STATIC"

    allow_stream.set()
    second_chunk = await asyncio.wait_for(stream_iter.__anext__(), timeout=0.2)
    assert second_chunk == "A"

    finish_stream.set()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(stream_iter.__anext__(), timeout=0.2)


async def test_schemas2obj_stream_mode_skip_avoids_waiting_for_streamable():
    broadcaster = StreamBroadcaster("llm")
    release_stream = asyncio.Event()

    async def produce():
        await release_stream.wait()
        await broadcaster.broadcast({"report": "A"})
        return {"report": "A"}

    broadcaster.create_task(produce())

    result = await asyncio.wait_for(
        schemas2obj(
            [
                ParameterSchema.model_validate(
                    {
                        "name": "llm",
                        "type": "string",
                        "value": {
                            "type": "ref",
                            "content": {"blockID": "llm", "path": "report"},
                        },
                    }
                )
            ],
            {"llm": broadcaster},
            stream_mode="skip",
        ),
        timeout=0.1,
    )

    assert result == {"llm": ""}
    release_stream.set()
