# tests/api/v1/test_project.py

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import status
from typing import Callable

# --- 核心模型和DAO导入 ---
from app.models import User, Workspace, Project, Team
from app.models.resource import ResourceRef
from app.dao.project.project_dao import ProjectDao
from app.dao.resource.resource_dao import ResourceInstanceDao

# --- 从 conftest.py 导入重构后的通用 Fixtures ---
from tests.conftest import (
    UserContext,
    registered_user_with_pro,
    registered_user_with_free,
    registered_user_with_team,
    auth_headers_factory,
    created_team,
    team_workspace,
    created_project_in_personal_ws,
    created_resource_factory,
)

# 将此模块中的所有测试都标记为异步执行
pytestmark = pytest.mark.asyncio

# ==============================================================================
# 1. 测试套件
# ==============================================================================

class TestProjectLifecycle:
    """测试项目在个人工作空间中的完整生命周期：创建、读取、更新和删除。"""

    async def test_create_project_in_personal_workspace(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext
    ):
        """[成功路径] 验证用户可以在自己的个人工作空间中成功创建项目。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        workspace_uuid = registered_user_with_pro.personal_workspace.uuid
        payload = {
            "name": "My Research Project",
            "description": "A project for my personal research.",
            "main_application_type": "uiapp"
        }
        
        response = await client.post(f"/api/v1/workspaces/{workspace_uuid}/projects", json=payload, headers=headers)
        
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()["data"]
        assert data["name"] == payload["name"]
        assert data["creator"]["uuid"] == registered_user_with_pro.user.uuid
        assert data["main_application_type"] == payload["main_application_type"]
        assert data["main_resource_uuid"] is not None

    async def test_list_projects_in_workspace(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, created_project_in_personal_ws: Project
    ):
        """[成功路径] 验证用户可以列出其工作空间中的所有项目。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        workspace_uuid = registered_user_with_pro.personal_workspace.uuid
        
        response = await client.get(f"/api/v1/workspaces/{workspace_uuid}/projects", headers=headers)
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert isinstance(data, list)
        assert len(data) >= 1
        
        project_uuids = {p["uuid"] for p in data}
        assert created_project_in_personal_ws.uuid in project_uuids

    async def test_list_projects_filter_by_main_application_type(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
    ):
        """[成功路径] 验证列表接口支持按主应用类型筛选。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        workspace_uuid = registered_user_with_pro.personal_workspace.uuid

        uiapp_payload = {"name": "UIApp Project", "main_application_type": "uiapp"}
        agent_payload = {"name": "Agent Project", "main_application_type": "agent"}

        response_uiapp = await client.post(
            f"/api/v1/workspaces/{workspace_uuid}/projects",
            json=uiapp_payload,
            headers=headers
        )
        assert response_uiapp.status_code == status.HTTP_201_CREATED, response_uiapp.text

        response_agent = await client.post(
            f"/api/v1/workspaces/{workspace_uuid}/projects",
            json=agent_payload,
            headers=headers
        )
        assert response_agent.status_code == status.HTTP_201_CREATED, response_agent.text
        agent_project_uuid = response_agent.json()["data"]["uuid"]

        list_response = await client.get(
            f"/api/v1/workspaces/{workspace_uuid}/projects?main_application_type=agent",
            headers=headers
        )
        assert list_response.status_code == status.HTTP_200_OK, list_response.text
        listed = list_response.json()["data"]
        listed_uuids = {item["uuid"] for item in listed}
        assert agent_project_uuid in listed_uuids
        assert all(item["main_application_type"] == "agent" for item in listed)

    async def test_get_project_details(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, created_project_in_personal_ws: Project
    ):
        """[成功路径] 验证用户可以获取自己项目的详细信息。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        response = await client.get(f"/api/v1/projects/{created_project_in_personal_ws.uuid}", headers=headers)
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["uuid"] == created_project_in_personal_ws.uuid
        assert data["name"] == created_project_in_personal_ws.name

    async def test_update_project(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, created_project_in_personal_ws: Project
    ):
        """[成功路径] 验证用户可以更新自己的项目。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        payload = {
            "name": "Updated Project Name",
            "description": "This description has been updated.",
            "visibility": "workspace" # Enum member 'workspace'
        }
        
        response = await client.put(f"/api/v1/projects/{created_project_in_personal_ws.uuid}", json=payload, headers=headers)
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["name"] == payload["name"]
        assert data["visibility"] == payload["visibility"]

    async def test_delete_project(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, created_project_in_personal_ws: Project
    ):
        """[成功路径][健壮性] 验证用户可以删除自己的项目，并通过二次查询确认删除成功。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        
        # Act: Delete the project
        response = await client.delete(f"/api/v1/projects/{created_project_in_personal_ws.uuid}", headers=headers)
        assert response.status_code == status.HTTP_200_OK

        # Assert: Verify the project is truly gone
        response_get = await client.get(f"/api/v1/projects/{created_project_in_personal_ws.uuid}", headers=headers)
        assert response_get.status_code == status.HTTP_404_NOT_FOUND

    async def test_get_project_dependency_graph(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        created_project_in_personal_ws: Project,
        created_resource_factory: Callable,
    ):
        """[成功路径] 验证项目依赖图可返回声明资源节点。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        resource = await created_resource_factory("tool")

        ref_payload = {"resource_uuid": resource.uuid}
        ref_response = await client.post(
            f"/api/v1/projects/{created_project_in_personal_ws.uuid}/resources",
            json=ref_payload,
            headers=headers,
        )
        assert ref_response.status_code == status.HTTP_201_CREATED, ref_response.text

        graph_response = await client.get(
            f"/api/v1/projects/{created_project_in_personal_ws.uuid}/dependency-graph",
            headers=headers,
        )
        assert graph_response.status_code == status.HTTP_200_OK, graph_response.text
        graph = graph_response.json()["data"]
        node_resource_uuids = {node["resource_uuid"] for node in graph["nodes"]}
        assert resource.uuid in node_resource_uuids

    async def test_project_dependency_graph_transitive_and_cycle_protection(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        created_project_in_personal_ws: Project,
        created_resource_factory: Callable,
        db_session: AsyncSession,
    ):
        """[成功路径] 递归依赖图应支持传递依赖并在循环依赖下稳定返回。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        project_uuid = created_project_in_personal_ws.uuid

        agent_a = await created_resource_factory("agent")
        agent_b = await created_resource_factory("agent")

        publish_a = await client.post(
            f"/api/v1/instances/{agent_a.workspace_instance.uuid}/publish",
            json={"version_tag": "1.0.0-a"},
            headers=headers,
        )
        assert publish_a.status_code == status.HTTP_201_CREATED, publish_a.text
        published_a_uuid = publish_a.json()["data"]["uuid"]

        publish_b = await client.post(
            f"/api/v1/instances/{agent_b.workspace_instance.uuid}/publish",
            json={"version_tag": "1.0.0-b"},
            headers=headers,
        )
        assert publish_b.status_code == status.HTTP_201_CREATED, publish_b.text
        published_b_uuid = publish_b.json()["data"]["uuid"]

        ref_payload = {"resource_uuid": agent_a.uuid}
        ref_response = await client.post(
            f"/api/v1/projects/{project_uuid}/resources",
            json=ref_payload,
            headers=headers,
        )
        assert ref_response.status_code == status.HTTP_201_CREATED, ref_response.text

        instance_dao = ResourceInstanceDao(db_session)
        published_a = await instance_dao.get_by_uuid(published_a_uuid)
        published_b = await instance_dao.get_by_uuid(published_b_uuid)
        assert published_a is not None and published_b is not None

        # workspace_a -> published_b (根节点到发布依赖)
        db_session.add(
            ResourceRef(
                source_resource_id=agent_a.id,
                source_instance_id=agent_a.workspace_instance.id,
                target_resource_id=agent_b.id,
                target_instance_id=published_b.id,
                source_node_uuid="node_workspace_a",
                alias="workspace_a_to_published_b",
            )
        )
        # published_b -> published_a (传递依赖)
        db_session.add(
            ResourceRef(
                source_resource_id=agent_b.id,
                source_instance_id=published_b.id,
                target_resource_id=agent_a.id,
                target_instance_id=published_a.id,
                source_node_uuid="node_published_b",
                alias="published_b_to_published_a",
            )
        )
        # published_a -> published_b (构造循环依赖，验证保护逻辑)
        db_session.add(
            ResourceRef(
                source_resource_id=agent_a.id,
                source_instance_id=published_a.id,
                target_resource_id=agent_b.id,
                target_instance_id=published_b.id,
                source_node_uuid="node_published_a",
                alias="published_a_to_published_b",
            )
        )
        await db_session.flush()

        graph_response = await client.get(
            f"/api/v1/projects/{project_uuid}/dependency-graph",
            headers=headers,
        )
        assert graph_response.status_code == status.HTTP_200_OK, graph_response.text
        graph = graph_response.json()["data"]
        node_instance_uuids = {node["instance_uuid"] for node in graph["nodes"]}
        edge_pairs = {(edge["source_instance_uuid"], edge["target_instance_uuid"]) for edge in graph["edges"]}

        assert agent_a.workspace_instance.uuid in node_instance_uuids
        assert published_b_uuid in node_instance_uuids
        assert published_a_uuid in node_instance_uuids
        assert (agent_a.workspace_instance.uuid, published_b_uuid) in edge_pairs
        assert (published_b_uuid, published_a_uuid) in edge_pairs

    async def test_project_env_config_crud(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        created_project_in_personal_ws: Project,
    ):
        """[成功路径] 验证项目环境配置的读取、更新与清空。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        project_uuid = created_project_in_personal_ws.uuid

        get_response = await client.get(
            f"/api/v1/projects/{project_uuid}/env-config",
            headers=headers,
        )
        assert get_response.status_code == status.HTTP_200_OK
        assert get_response.json()["data"]["env_config"] == {}

        update_payload = {"env_config": {"default_prompt": "hello", "workflow_uuid": "wf-123"}}
        update_response = await client.put(
            f"/api/v1/projects/{project_uuid}/env-config",
            json=update_payload,
            headers=headers,
        )
        assert update_response.status_code == status.HTTP_200_OK
        assert update_response.json()["data"]["env_config"] == update_payload["env_config"]

        clear_response = await client.delete(
            f"/api/v1/projects/{project_uuid}/env-config",
            headers=headers,
        )
        assert clear_response.status_code == status.HTTP_200_OK

        get_after_clear = await client.get(
            f"/api/v1/projects/{project_uuid}/env-config",
            headers=headers,
        )
        assert get_after_clear.status_code == status.HTTP_200_OK
        assert get_after_clear.json()["data"]["env_config"] == {}

class TestProjectPermissions:
    """测试项目的权限隔离，权限继承自其所在的Workspace。"""

    async def test_cannot_create_project_in_others_workspace(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, registered_user_with_free: UserContext
    ):
        """[失败路径] 验证用户不能在他人工作空间中创建项目。"""
        # another_user 尝试在 user 的个人空间里创建项目
        intruder_headers = await auth_headers_factory(registered_user_with_free)
        target_workspace_uuid = registered_user_with_pro.personal_workspace.uuid
        payload = {"name": "Invasion Project", "main_application_type": "uiapp"}
        
        response = await client.post(f"/api/v1/workspaces/{target_workspace_uuid}/projects", json=payload, headers=intruder_headers)
        
        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_cannot_list_projects_in_others_workspace(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, registered_user_with_free: UserContext
    ):
        """[失败路径] 验证用户不能列出他人工作空间中的项目。"""
        intruder_headers = await auth_headers_factory(registered_user_with_free)
        target_workspace_uuid = registered_user_with_pro.personal_workspace.uuid
        
        response = await client.get(f"/api/v1/workspaces/{target_workspace_uuid}/projects", headers=intruder_headers)
        
        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_cannot_get_others_project_details(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_free: UserContext, created_project_in_personal_ws: Project
    ):
        """[失败路径] 验证用户不能获取他人项目的详细信息。"""
        intruder_headers = await auth_headers_factory(registered_user_with_free)
        target_project_uuid = created_project_in_personal_ws.uuid
        
        response = await client.get(f"/api/v1/projects/{target_project_uuid}", headers=intruder_headers)
        
        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_cannot_update_others_project(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_free: UserContext, created_project_in_personal_ws: Project
    ):
        """[失败路径] 验证用户不能更新他人的项目。"""
        intruder_headers = await auth_headers_factory(registered_user_with_free)
        target_project_uuid = created_project_in_personal_ws.uuid
        payload = {"name": "Hijacked Project Name"}
        
        response = await client.put(f"/api/v1/projects/{target_project_uuid}", json=payload, headers=intruder_headers)
        
        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_cannot_delete_others_project(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_free: UserContext, created_project_in_personal_ws: Project
    ):
        """[失败路径] 验证用户不能删除他人的项目。"""
        intruder_headers = await auth_headers_factory(registered_user_with_free)
        target_project_uuid = created_project_in_personal_ws.uuid
        
        response = await client.delete(f"/api/v1/projects/{target_project_uuid}", headers=intruder_headers)
        
        assert response.status_code == status.HTTP_403_FORBIDDEN

class TestAdvancedProjectFeatures:
    """[待办清单] 测试未来需要实现的高级项目功能。"""

    @pytest.mark.skip(reason="Team member invitation and role system not fully implemented for testing.")
    async def test_team_member_can_access_project_in_team_workspace(
        self, client: AsyncClient, auth_headers_factory: Callable, created_project_in_team_ws: Project
    ):
        """
        [待办] 验证普通团队成员（非Owner）可以访问其团队工作空间中的项目。
        - Arrange: 邀请一个新用户 new_member 作为 'team:member' 加入团队。
        - Arrange: new_member 接受邀请。
        - Arrange: 使用 new_member 的 token 获取 headers。
        - Act: 尝试 GET /api/v1/projects/{created_project_in_team_ws.uuid}。
        - Assert: 响应码应为 200 OK。
        """
        pass

    @pytest.mark.skip(reason="API for setting a project's main resource is not implemented.")
    async def test_set_main_resource_for_project(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, created_project_in_personal_ws: Project
    ):
        """
        [待办] 验证用户可以为项目设置一个主入口资源。
        - API: PUT /api/v1/projects/{project_uuid}/main-resource
        - Arrange: 在项目中创建一个可作为主资源的 Resource (e.g., uiapp)。
        - Act: 调用API，将该资源的UUID设置为主资源。
        - Assert: 再次获取项目详情，验证 'main_resource_id' 字段已更新。
        """
        pass

    @pytest.mark.skip(reason="API for project templates is not implemented.")
    async def test_project_template_lifecycle(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, created_project_in_personal_ws: Project
    ):
        """
        [待办] 验证完整的项目模板生命周期：创建模板 -> 使用模板创建新项目。
        - API 1: POST /api/v1/projects/{project_uuid}/template (将项目另存为模板)
        - API 2: POST /api/v1/workspaces/{workspace_uuid}/projects?from_template={template_uuid}
        - Act 1: 调用 API 1 将 `created_project_in_personal_ws` 标记为模板。
        - Assert 1: 验证其 status 变为 'template'。
        - Act 2: 调用 API 2，使用该模板在同一个工作空间中创建新项目。
        - Assert 2: 验证新项目被创建，且其内容（如资源列表）与模板项目一致。
        """
        pass

    @pytest.mark.skip(reason="API for project sharing is not implemented.")
    async def test_share_project_with_another_user(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, registered_user_with_free: UserContext, created_project_in_personal_ws: Project
    ):
        """
        [待办] 验证项目所有者可以将项目以只读权限分享给另一个用户。
        - API: POST /api/v1/projects/{project_uuid}/shares
        - Arrange: 获取 owner (registered_user_with_pro) 的 headers。
        - Act: 调用分享API，payload 为 {'user_uuid': registered_user_with_free.user.uuid, 'permission': 'read'}。
        - Assert: 响应码为 201 Created。
        - Arrange: 获取被分享者 (registered_user_with_free) 的 headers。
        - Act: 尝试 GET /api/v1/projects/{created_project_in_personal_ws.uuid}。
        - Assert: 响应码应为 200 OK (可以访问)。
        - Act: 尝试 PUT /api/v1/projects/{created_project_in_personal_ws.uuid}。
        - Assert: 响应码应为 403 Forbidden (不能编辑)。
        """
        pass
