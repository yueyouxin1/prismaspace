# tests/api/v1/test_workflow_runtime.py

import pytest
from httpx import AsyncClient
from typing import Callable
from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import UserContext
from app.models.resource import Resource
from app.system.resource.workflow.node_def_manager import NodeDefManager

pytestmark = pytest.mark.asyncio


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
        assert isinstance(loop_node["forms"], list)
        assert any(form.get("output_key") == "config.loopType" for form in loop_node["forms"])

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
