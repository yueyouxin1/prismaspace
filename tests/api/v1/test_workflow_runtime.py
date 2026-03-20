# tests/api/v1/test_workflow_runtime.py

import asyncio

import pytest
from httpx import AsyncClient
from typing import Callable
from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import UserContext
from app.models.resource import Resource
from app.schemas.resource.workflow.workflow_schemas import WorkflowExecutionRequest
from app.system.resource.workflow.node_def_manager import NodeDefManager
from app.engine.workflow import (
    BaseNodeConfig,
    NodeCategory,
    NodeData,
    NodeExecutionResult,
    NodeResultData,
    NodeTemplate,
    ParameterSchema,
    register_node,
)
from app.engine.workflow.registry import BaseNode
from app.engine.utils.parameter_schema_utils import schemas2obj

pytestmark = pytest.mark.asyncio


class _DebugEchoConfig(BaseNodeConfig):
    pass


class _SlowLiveConfig(BaseNodeConfig):
    pass


DEBUG_ECHO_TEMPLATE = NodeTemplate(
    category=NodeCategory.CUSTOM,
    icon="zap",
    data=NodeData(
        registryId="DebugEchoNode",
        name="Debug Echo Node",
        description="Echo for workflow debug tests.",
        inputs=[ParameterSchema(name="text", type="string")],
        outputs=[ParameterSchema(name="echo", type="string")],
        config=_DebugEchoConfig(),
    ),
    forms=[],
)


SLOW_LIVE_TEMPLATE = NodeTemplate(
    category=NodeCategory.CUSTOM,
    icon="clock-3",
    data=NodeData(
        registryId="ApiSlowLiveNode",
        name="API Slow Live Node",
        description="Sleep briefly to validate /live attach.",
        inputs=[ParameterSchema(name="text", type="string", required=True, open=True)],
        outputs=[ParameterSchema(name="echo", type="string", required=True, open=True)],
        config=_SlowLiveConfig(),
    ),
    forms=[],
)


@register_node(template=DEBUG_ECHO_TEMPLATE)
class DebugEchoNode(BaseNode):
    async def execute(self) -> NodeExecutionResult:
        node_input = await schemas2obj(self.node.data.inputs, self.context.variables)
        return NodeExecutionResult(
            input=node_input,
            data=NodeResultData(output={"echo": f"echo:{node_input.get('text', '')}"}),
        )


@register_node(template=SLOW_LIVE_TEMPLATE)
class ApiSlowLiveNode(BaseNode):
    async def execute(self) -> NodeExecutionResult:
        node_input = await schemas2obj(self.node.data.inputs, self.context.variables)
        await asyncio.sleep(0.2)
        return NodeExecutionResult(
            input=node_input,
            data=NodeResultData(output={"echo": f"echo:{node_input.get('text', '')}"}),
        )


@pytest.fixture
async def synced_workflow_node_defs(db_session: AsyncSession):
    await NodeDefManager(db_session).sync_nodes()
    await db_session.flush()


@pytest.fixture
async def workflow_resource(created_resource_factory: Callable) -> Resource:
    return await created_resource_factory("workflow")


class TestWorkflowRuntimeApi:
    async def test_list_workflow_node_definitions(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        synced_workflow_node_defs,
    ):
        headers = await auth_headers_factory(registered_user_with_pro)
        response = await client.get("/api/v1/workflow/nodes", headers=headers)

        assert response.status_code == status.HTTP_200_OK, response.text
        payload = response.json()
        nodes = payload["data"]
        assert isinstance(nodes, list)
        assert len(nodes) > 0

        registry_ids = {item["node_uid"] for item in nodes}
        assert "Start" in registry_ids
        assert "End" in registry_ids

        first = nodes[0]
        assert "label" in first
        assert "node" in first
        assert isinstance(first["node"], dict)
        loop_node = next((item for item in nodes if item["node_uid"] == "Loop"), None)
        assert loop_node is not None
        assert "forms" not in loop_node

    async def test_update_validate_execute_workflow_instance(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        workflow_resource: Resource,
    ):
        headers = await auth_headers_factory(registered_user_with_pro)
        instance_uuid = workflow_resource.workspace_instance.uuid

        graph_payload = {
            "graph": {
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
                        "position": {"x": 100, "y": 200},
                    },
                    {
                        "id": "end",
                        "data": {
                            "registryId": "End",
                            "name": "End",
                            "inputs": [],
                            "outputs": [],
                            "config": {"returnType": "Object"},
                        },
                        "position": {"x": 420, "y": 200},
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
                "viewport": {"x": 0, "y": 0, "zoom": 1},
            }
        }

        update_response = await client.put(
            f"/api/v1/instances/{instance_uuid}",
            json=graph_payload,
            headers=headers,
        )
        assert update_response.status_code == status.HTTP_200_OK, update_response.text
        updated = update_response.json()["data"]
        assert updated["uuid"] == instance_uuid
        assert updated["graph"]["nodes"][0]["data"]["registryId"] == "Start"

        validate_response = await client.post(
            f"/api/v1/workflow/{instance_uuid}/validate",
            headers=headers,
        )
        assert validate_response.status_code == status.HTTP_200_OK, validate_response.text
        validation = validate_response.json()["data"]
        assert validation["is_valid"] is True
        assert validation["errors"] == []

        execute_response = await client.post(
            f"/api/v1/workflow/{instance_uuid}/execute",
            json={"inputs": {"hello": "world"}},
            headers=headers,
        )
        assert execute_response.status_code == status.HTTP_200_OK, execute_response.text
        execute_data = execute_response.json()["data"]
        assert execute_data["success"] is True
        assert isinstance(execute_data["data"]["output"], dict)
        assert isinstance(execute_data["data"]["run_id"], str)
        assert isinstance(execute_data["data"]["thread_id"], str)

        run_id = execute_data["data"]["run_id"]
        run_response = await client.get(
            f"/api/v1/workflow/runs/{run_id}",
            headers=headers,
        )
        assert run_response.status_code == status.HTTP_200_OK, run_response.text
        run_payload = run_response.json()["data"]
        assert run_payload["run_id"] == run_id
        assert run_payload["status"] == "succeeded"
        assert run_payload["workflow_instance_uuid"] == instance_uuid
        assert run_payload["latest_checkpoint"] is not None
        assert len(run_payload["node_executions"]) >= 2

    async def test_start_end_minimal_flow_maps_workflow_input_to_output(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        workflow_resource: Resource,
    ):
        headers = await auth_headers_factory(registered_user_with_pro)
        instance_uuid = workflow_resource.workspace_instance.uuid

        graph_payload = {
            "graph": {
                "nodes": [
                    {
                        "id": "start",
                        "data": {
                            "registryId": "Start",
                            "name": "Start",
                            "inputs": [],
                            "outputs": [
                                {
                                    "name": "message",
                                    "type": "string",
                                    "required": True,
                                    "open": True,
                                }
                            ],
                            "config": {},
                        },
                        "position": {"x": 100, "y": 200},
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
                                    "required": True,
                                    "open": True,
                                    "value": {
                                        "type": "ref",
                                        "content": {"blockID": "start", "path": "message"},
                                    },
                                }
                            ],
                            "outputs": [],
                            "config": {"returnType": "Object", "stream": False},
                        },
                        "position": {"x": 420, "y": 200},
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
                "viewport": {"x": 0, "y": 0, "zoom": 1},
            }
        }

        update_response = await client.put(
            f"/api/v1/instances/{instance_uuid}",
            json=graph_payload,
            headers=headers,
        )
        assert update_response.status_code == status.HTTP_200_OK, update_response.text
        updated = update_response.json()["data"]
        assert len(updated["inputs_schema"]) == 1
        assert updated["inputs_schema"][0]["name"] == "message"
        assert updated["inputs_schema"][0]["type"] == "string"
        assert updated["inputs_schema"][0]["required"] is True
        assert updated["inputs_schema"][0]["open"] is True

        assert len(updated["outputs_schema"]) == 1
        assert updated["outputs_schema"][0]["name"] == "result"
        assert updated["outputs_schema"][0]["type"] == "string"
        assert updated["outputs_schema"][0]["required"] is True
        assert updated["outputs_schema"][0]["open"] is True
        assert updated["outputs_schema"][0]["value"] == {
            "type": "ref",
            "content": {"blockID": "start", "path": "message"},
        }

        validate_response = await client.post(
            f"/api/v1/workflow/{instance_uuid}/validate",
            headers=headers,
        )
        assert validate_response.status_code == status.HTTP_200_OK, validate_response.text
        validation = validate_response.json()["data"]
        assert validation["is_valid"] is True
        assert validation["errors"] == []

        execute_response = await client.post(
            f"/api/v1/workflow/{instance_uuid}/execute",
            json={"inputs": {"message": "hello workflow"}},
            headers=headers,
        )
        assert execute_response.status_code == status.HTTP_200_OK, execute_response.text
        execute_payload = execute_response.json()["data"]["data"]
        assert execute_payload["output"] == {"result": "hello workflow"}
        assert execute_payload["content"] is None

    async def test_reject_invalid_workflow_graph_with_cycle(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        workflow_resource: Resource,
    ):
        headers = await auth_headers_factory(registered_user_with_pro)
        instance_uuid = workflow_resource.workspace_instance.uuid

        invalid_graph_payload = {
            "graph": {
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
                        "position": {"x": 100, "y": 200},
                    },
                    {
                        "id": "end",
                        "data": {
                            "registryId": "End",
                            "name": "End",
                            "inputs": [],
                            "outputs": [],
                            "config": {"returnType": "Object"},
                        },
                        "position": {"x": 420, "y": 200},
                    },
                ],
                "edges": [
                    {
                        "sourceNodeID": "start",
                        "targetNodeID": "end",
                        "sourcePortID": "0",
                        "targetPortID": "0",
                    },
                    {
                        "sourceNodeID": "end",
                        "targetNodeID": "start",
                        "sourcePortID": "0",
                        "targetPortID": "0",
                    },
                ],
            }
        }

        response = await client.put(
            f"/api/v1/instances/{instance_uuid}",
            json=invalid_graph_payload,
            headers=headers,
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.text
        assert "Invalid workflow graph structure" in response.json()["msg"]

    async def test_validate_detects_invalid_reference_path(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        workflow_resource: Resource,
    ):
        headers = await auth_headers_factory(registered_user_with_pro)
        instance_uuid = workflow_resource.workspace_instance.uuid

        graph_payload = {
            "graph": {
                "nodes": [
                    {
                        "id": "start",
                        "data": {
                            "registryId": "Start",
                            "name": "Start",
                            "inputs": [],
                            "outputs": [{"name": "hello", "type": "string"}],
                            "config": {},
                        },
                        "position": {"x": 100, "y": 200},
                    },
                    {
                        "id": "end",
                        "data": {
                            "registryId": "End",
                            "name": "End",
                            "inputs": [
                                {
                                    "name": "out",
                                    "type": "string",
                                    "value": {
                                        "type": "ref",
                                        "content": {"blockID": "start", "path": "missing"},
                                    },
                                }
                            ],
                            "outputs": [],
                            "config": {"returnType": "Object"},
                        },
                        "position": {"x": 420, "y": 200},
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
        }

        update_response = await client.put(
            f"/api/v1/instances/{instance_uuid}",
            json=graph_payload,
            headers=headers,
        )
        assert update_response.status_code == status.HTTP_200_OK, update_response.text

        validate_response = await client.post(
            f"/api/v1/workflow/{instance_uuid}/validate",
            headers=headers,
        )
        assert validate_response.status_code == status.HTTP_200_OK, validate_response.text
        payload = validate_response.json()["data"]
        assert payload["is_valid"] is False
        assert any("path 'missing' not found" in err for err in payload["errors"])

    async def test_validate_detects_invalid_loop_output_reference(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        workflow_resource: Resource,
    ):
        headers = await auth_headers_factory(registered_user_with_pro)
        instance_uuid = workflow_resource.workspace_instance.uuid

        graph_payload = {
            "graph": {
                "nodes": [
                    {
                        "id": "start",
                        "data": {
                            "registryId": "Start",
                            "name": "Start",
                            "inputs": [],
                            "outputs": [{"name": "list", "type": "array", "items": {"type": "string"}}],
                            "config": {},
                        },
                        "position": {"x": 100, "y": 200},
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
                                            "blockID": "missing_block",
                                            "path": "res",
                                        },
                                    },
                                }
                            ],
                            "config": {
                                "loopType": "list",
                                "loopList": {
                                    "name": "loopList",
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "value": {"type": "literal", "content": ["a", "b"]},
                                },
                            },
                        },
                        "position": {"x": 280, "y": 200},
                    },
                    {
                        "id": "end",
                        "data": {
                            "registryId": "End",
                            "name": "End",
                            "inputs": [
                                {
                                    "name": "out",
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
                        "position": {"x": 520, "y": 200},
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
        }

        update_response = await client.put(
            f"/api/v1/instances/{instance_uuid}",
            json=graph_payload,
            headers=headers,
        )
        assert update_response.status_code == status.HTTP_200_OK, update_response.text

        validate_response = await client.post(
            f"/api/v1/workflow/{instance_uuid}/validate",
            headers=headers,
        )
        assert validate_response.status_code == status.HTTP_200_OK, validate_response.text
        payload = validate_response.json()["data"]
        assert payload["is_valid"] is False
        assert any("missing_block" in err for err in payload["errors"])

    async def test_interrupt_resume_and_run_listing(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        workflow_resource: Resource,
    ):
        headers = await auth_headers_factory(registered_user_with_pro)
        instance_uuid = workflow_resource.workspace_instance.uuid

        graph_payload = {
            "graph": {
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
                        "id": "interrupt",
                        "data": {
                            "registryId": "Interrupt",
                            "name": "Approval",
                            "inputs": [],
                            "outputs": [{"name": "resume", "type": "object"}],
                            "config": {
                                "reason": "approval_required",
                                "message": "Please confirm the workflow run.",
                                "resume_output_key": "resume",
                            },
                        },
                    },
                    {
                        "id": "end",
                        "data": {
                            "registryId": "End",
                            "name": "End",
                            "inputs": [
                                {
                                    "name": "approval",
                                    "type": "object",
                                    "value": {
                                        "type": "ref",
                                        "content": {"blockID": "interrupt", "path": "resume"},
                                    },
                                }
                            ],
                            "outputs": [],
                            "config": {"returnType": "Object"},
                        },
                    },
                ],
                "edges": [
                    {"sourceNodeID": "start", "targetNodeID": "interrupt", "sourcePortID": "0", "targetPortID": "0"},
                    {"sourceNodeID": "interrupt", "targetNodeID": "end", "sourcePortID": "0", "targetPortID": "0"},
                ],
            }
        }

        update_response = await client.put(
            f"/api/v1/instances/{instance_uuid}",
            json=graph_payload,
            headers=headers,
        )
        assert update_response.status_code == status.HTTP_200_OK, update_response.text

        execute_response = await client.post(
            f"/api/v1/workflow/{instance_uuid}/execute",
            json={"inputs": {}},
            headers=headers,
        )
        assert execute_response.status_code == status.HTTP_200_OK, execute_response.text
        execute_data = execute_response.json()["data"]["data"]
        assert execute_data["outcome"] == "interrupt"
        assert execute_data["interrupt"]["reason"] == "approval_required"
        run_id = execute_data["run_id"]

        run_response = await client.get(
            f"/api/v1/workflow/runs/{run_id}",
            headers=headers,
        )
        assert run_response.status_code == status.HTTP_200_OK, run_response.text
        run_payload = run_response.json()["data"]
        assert run_payload["status"] == "interrupted"
        assert run_payload["can_resume"] is True

        resume_response = await client.post(
            f"/api/v1/workflow/{instance_uuid}/execute",
            json={
                "resume_from_run_id": run_id,
                "meta": {"resume": {"resume": {"approved": True}}},
            },
            headers=headers,
        )
        assert resume_response.status_code == status.HTTP_200_OK, resume_response.text
        resumed_data = resume_response.json()["data"]["data"]
        assert resumed_data["outcome"] == "success"
        assert resumed_data["output"]["approval"]["approved"] is True

        runs_response = await client.get(
            f"/api/v1/workflow/{instance_uuid}/runs",
            headers=headers,
        )
        assert runs_response.status_code == status.HTTP_200_OK, runs_response.text
        runs_payload = runs_response.json()["data"]
        assert len(runs_payload) >= 2
        assert runs_payload[0]["run_id"] == resumed_data["run_id"]

    async def test_debug_node_execute_uses_compiled_subgraph(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        workflow_resource: Resource,
    ):
        headers = await auth_headers_factory(registered_user_with_pro)
        instance_uuid = workflow_resource.workspace_instance.uuid

        graph_payload = {
            "graph": {
                "nodes": [
                    {
                        "id": "start",
                        "data": {
                            "registryId": "Start",
                            "name": "Start",
                            "inputs": [],
                            "outputs": [{"name": "text", "type": "string"}],
                            "config": {},
                        },
                    },
                    {
                        "id": "debug_echo",
                        "data": {
                            "registryId": "DebugEchoNode",
                            "name": "Echo",
                            "inputs": [
                                {
                                    "name": "text",
                                    "type": "string",
                                    "value": {
                                        "type": "ref",
                                        "content": {"blockID": "start", "path": "text"},
                                    },
                                }
                            ],
                            "outputs": [{"name": "echo", "type": "string"}],
                            "config": {},
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
                                    "value": {
                                        "type": "ref",
                                        "content": {"blockID": "debug_echo", "path": "echo"},
                                    },
                                }
                            ],
                            "outputs": [],
                            "config": {"returnType": "Object"},
                        },
                    },
                ],
                "edges": [
                    {"sourceNodeID": "start", "targetNodeID": "debug_echo", "sourcePortID": "0", "targetPortID": "0"},
                    {"sourceNodeID": "debug_echo", "targetNodeID": "end", "sourcePortID": "0", "targetPortID": "0"},
                ],
            }
        }

        update_response = await client.put(
            f"/api/v1/instances/{instance_uuid}",
            json=graph_payload,
            headers=headers,
        )
        assert update_response.status_code == status.HTTP_200_OK, update_response.text

        debug_response = await client.post(
            f"/api/v1/workflow/{instance_uuid}/nodes/debug_echo/debug",
            json={"inputs": {"text": "hello"}},
            headers=headers,
        )
        assert debug_response.status_code == status.HTTP_200_OK, debug_response.text
        debug_payload = debug_response.json()["data"]["data"]
        assert debug_payload["output"]["echo"] == "echo:hello"

    async def test_subworkflow_node_executes_child_workflow_and_records_lineage(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        workflow_resource: Resource,
        created_resource_factory: Callable,
    ):
        headers = await auth_headers_factory(registered_user_with_pro)
        parent_instance_uuid = workflow_resource.workspace_instance.uuid
        child_resource = await created_resource_factory("workflow")
        child_instance_uuid = child_resource.workspace_instance.uuid

        child_graph = {
            "graph": {
                "nodes": [
                    {
                        "id": "start",
                        "data": {
                            "registryId": "Start",
                            "name": "Start",
                            "inputs": [],
                            "outputs": [{"name": "text", "type": "string"}],
                            "config": {},
                        },
                    },
                    {
                        "id": "debug_echo",
                        "data": {
                            "registryId": "DebugEchoNode",
                            "name": "Echo",
                            "inputs": [
                                {
                                    "name": "text",
                                    "type": "string",
                                    "value": {
                                        "type": "ref",
                                        "content": {"blockID": "start", "path": "text"},
                                    },
                                }
                            ],
                            "outputs": [{"name": "echo", "type": "string"}],
                            "config": {},
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
                                    "value": {
                                        "type": "ref",
                                        "content": {"blockID": "debug_echo", "path": "echo"},
                                    },
                                }
                            ],
                            "outputs": [],
                            "config": {"returnType": "Object"},
                        },
                    },
                ],
                "edges": [
                    {"sourceNodeID": "start", "targetNodeID": "debug_echo", "sourcePortID": "0", "targetPortID": "0"},
                    {"sourceNodeID": "debug_echo", "targetNodeID": "end", "sourcePortID": "0", "targetPortID": "0"},
                ],
            }
        }
        child_update = await client.put(
            f"/api/v1/instances/{child_instance_uuid}",
            json=child_graph,
            headers=headers,
        )
        assert child_update.status_code == status.HTTP_200_OK, child_update.text

        parent_graph = {
            "graph": {
                "nodes": [
                    {
                        "id": "start",
                        "data": {
                            "registryId": "Start",
                            "name": "Start",
                            "inputs": [],
                            "outputs": [{"name": "text", "type": "string"}],
                            "config": {},
                        },
                    },
                    {
                        "id": "child_flow",
                        "data": {
                            "registryId": "WorkflowNode",
                            "name": "Child Flow",
                            "inputs": [
                                {
                                    "name": "text",
                                    "type": "string",
                                    "value": {
                                        "type": "ref",
                                        "content": {"blockID": "start", "path": "text"},
                                    },
                                }
                            ],
                            "outputs": [{"name": "result", "type": "string"}],
                            "config": {"resource_instance_uuid": child_instance_uuid},
                        },
                    },
                    {
                        "id": "end",
                        "data": {
                            "registryId": "End",
                            "name": "End",
                            "inputs": [
                                {
                                    "name": "final",
                                    "type": "string",
                                    "value": {
                                        "type": "ref",
                                        "content": {"blockID": "child_flow", "path": "result"},
                                    },
                                }
                            ],
                            "outputs": [],
                            "config": {"returnType": "Object"},
                        },
                    },
                ],
                "edges": [
                    {"sourceNodeID": "start", "targetNodeID": "child_flow", "sourcePortID": "0", "targetPortID": "0"},
                    {"sourceNodeID": "child_flow", "targetNodeID": "end", "sourcePortID": "0", "targetPortID": "0"},
                ],
            }
        }
        parent_update = await client.put(
            f"/api/v1/instances/{parent_instance_uuid}",
            json=parent_graph,
            headers=headers,
        )
        assert parent_update.status_code == status.HTTP_200_OK, parent_update.text

        execute_response = await client.post(
            f"/api/v1/workflow/{parent_instance_uuid}/execute",
            json={"inputs": {"text": "nested"}},
            headers=headers,
        )
        assert execute_response.status_code == status.HTTP_200_OK, execute_response.text
        execute_data = execute_response.json()["data"]["data"]
        assert execute_data["output"]["final"] == "echo:nested"

        run_response = await client.get(
            f"/api/v1/workflow/runs/{execute_data['run_id']}",
            headers=headers,
        )
        assert run_response.status_code == status.HTTP_200_OK, run_response.text
        run_payload = run_response.json()["data"]
        child_node = next(node for node in run_payload["node_executions"] if node["node_id"] == "child_flow")
        assert child_node["result"]["output"]["__meta__"]["child_workflow_uuid"] == child_instance_uuid
        assert isinstance(child_node["result"]["output"]["__meta__"]["child_run_id"], str)

    async def test_async_workflow_submission_enqueues_background_run(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        workflow_resource: Resource,
        arq_pool_mock,
    ):
        headers = await auth_headers_factory(registered_user_with_pro)
        instance_uuid = workflow_resource.workspace_instance.uuid

        response = await client.post(
            f"/api/v1/workflow/{instance_uuid}/async",
            json={"inputs": {"hello": "async"}},
            headers=headers,
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        payload = response.json()["data"]
        assert payload["status"] == "pending"
        assert isinstance(payload["run_id"], str)
        assert isinstance(payload["thread_id"], str)
        arq_pool_mock.enqueue_job.assert_awaited()

    async def test_workflow_run_events_and_replay_are_queryable(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        workflow_resource: Resource,
    ):
        headers = await auth_headers_factory(registered_user_with_pro)
        instance_uuid = workflow_resource.workspace_instance.uuid

        execute_response = await client.post(
            f"/api/v1/workflow/{instance_uuid}/execute",
            json={"inputs": {"hello": "events"}},
            headers=headers,
        )
        assert execute_response.status_code == status.HTTP_200_OK, execute_response.text
        run_id = execute_response.json()["data"]["data"]["run_id"]

        events_response = await client.get(
            f"/api/v1/workflow/runs/{run_id}/events",
            headers=headers,
        )
        assert events_response.status_code == status.HTTP_200_OK, events_response.text
        events_payload = events_response.json()["data"]
        assert len(events_payload) >= 3
        assert events_payload[0]["event_type"] == "start"
        assert events_payload[-1]["event_type"] == "finish"

        async with client.stream(
            "GET",
            f"/api/v1/workflow/runs/{run_id}/replay",
            headers=headers,
        ) as replay_response:
            assert replay_response.status_code == status.HTTP_200_OK
            body = ""
            async for chunk in replay_response.aiter_text():
                body += chunk

        assert "event: start" in body
        assert "event: finish" in body

    async def test_workflow_live_endpoint_streams_detached_run_by_run_id(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        workflow_resource: Resource,
        app_context_factory,
    ):
        headers = await auth_headers_factory(registered_user_with_pro)
        instance_uuid = workflow_resource.workspace_instance.uuid

        graph_payload = {
            "graph": {
                "nodes": [
                    {
                        "id": "start",
                        "data": {
                            "registryId": "Start",
                            "name": "Start",
                            "inputs": [],
                            "outputs": [{"name": "text", "type": "string", "required": True, "open": True}],
                            "config": {},
                        },
                    },
                    {
                        "id": "slow",
                        "data": {
                            "registryId": "ApiSlowLiveNode",
                            "name": "Slow",
                            "inputs": [
                                {
                                    "name": "text",
                                    "type": "string",
                                    "required": True,
                                    "open": True,
                                    "value": {
                                        "type": "ref",
                                        "content": {"blockID": "start", "path": "text"},
                                    },
                                }
                            ],
                            "outputs": [{"name": "echo", "type": "string", "required": True, "open": True}],
                            "config": {},
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
                                    "required": True,
                                    "open": True,
                                    "value": {
                                        "type": "ref",
                                        "content": {"blockID": "slow", "path": "echo"},
                                    },
                                }
                            ],
                            "outputs": [],
                            "config": {"returnType": "Object"},
                        },
                    },
                ],
                "edges": [
                    {"sourceNodeID": "start", "targetNodeID": "slow", "sourcePortID": "0", "targetPortID": "0"},
                    {"sourceNodeID": "slow", "targetNodeID": "end", "sourcePortID": "0", "targetPortID": "0"},
                ],
            }
        }

        update_response = await client.put(
            f"/api/v1/instances/{instance_uuid}",
            json=graph_payload,
            headers=headers,
        )
        assert update_response.status_code == status.HTTP_200_OK, update_response.text

        app_context = await app_context_factory(registered_user_with_pro.user)
        from app.services.resource.workflow.workflow_service import WorkflowService

        service = WorkflowService(app_context)
        run_result = await service.async_execute(
            instance_uuid,
            WorkflowExecutionRequest(inputs={"text": "live-api"}),
            registered_user_with_pro.user,
        )

        first_event = await run_result.generator.get()
        assert first_event.event == "start"
        run_id = str(first_event.data["run_id"])
        assert first_event.id == "1"
        assert callable(run_result.detach)
        run_result.detach()

        async with client.stream(
            "GET",
            f"/api/v1/workflow/runs/{run_id}/live",
            params={"after_seq": 1},
            headers=headers,
        ) as live_response:
            assert live_response.status_code == status.HTTP_200_OK, live_response.text
            body = ""
            async for chunk in live_response.aiter_text():
                body += chunk
                if "event: finish" in body:
                    break

        assert "event: node_start" in body
        assert "event: finish" in body
        assert '"result": "echo:live-api"' in body

        if run_result.task:
            await run_result.task
