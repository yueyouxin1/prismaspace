# tests/api/v1/test_resource.py

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import status
from typing import Callable

# --- 从 conftest 导入重构后的通用 Fixtures ---
from tests.conftest import (
    registered_user_with_pro, 
    registered_user_with_free, 
    created_project_in_personal_ws,
    UserContext
)

from app.models import User, Project
from app.models.resource import Resource, ResourceInstance, VersionStatus
# 导入所有可能的子类以进行类型检查
from app.models.resource.tool import Tool
from app.models.resource.knowledge import KnowledgeBase
from app.models.resource.tenantdb import TenantDB
from app.dao.resource.resource_dao import ResourceDao, ResourceInstanceDao

# 将所有测试标记为异步
pytestmark = pytest.mark.asyncio

# ==============================================================================
# 1. 核心 Fixtures (本文件提供，可被其他测试文件导入)
# ==============================================================================

@pytest.fixture(params=["tool"]) # "knowledge" 也可以加入
def resource_type(request):
    """一个参数化的 Fixture，为通用测试提供不同的资源类型。"""
    return request.param

@pytest.fixture
async def created_resource(created_resource_factory: Callable, resource_type: str) -> Resource:
    """
    一个依赖于参数化 fixture 的便捷 fixture，用于在本文件中运行通用测试。
    """
    return await created_resource_factory(resource_type)


# ==============================================================================
# 2. 通用资源测试套件
# ==============================================================================

class TestGenericResourceLifecycle:
    """[通用] 测试所有资源类型都必须遵守的通用创建和列出逻辑。"""

    async def test_create_resource_success(
        self, created_resource_factory: Callable, db_session: AsyncSession, resource_type: str
    ):
        """[参数化] 验证为不同类型资源创建时，API响应和数据库状态都正确。"""
        # Act: 使用工厂创建资源
        resource = await created_resource_factory(resource_type)
        
        # Assert: 验证数据库状态
        assert resource.name == f"My First {resource_type.capitalize()}"
        assert resource.resource_type.name == resource_type
        assert resource.workspace_instance is not None
        assert resource.workspace_instance.status == VersionStatus.WORKSPACE
        
        type_map = {"tool": Tool, "knowledge": KnowledgeBase, "tenantdb": TenantDB}
        expected_type = type_map.get(resource_type)
        assert isinstance(resource.workspace_instance, expected_type), f"Instance should be of type {expected_type}"

    async def test_list_resources_in_workspace(self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, created_resource: Resource):
        """[参数化] 验证可以成功列出工作空间中的资源。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        workspace_uuid = registered_user_with_pro.personal_workspace.uuid
        response = await client.get(f"/api/v1/workspaces/{workspace_uuid}/resources", headers=headers)
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert len(data) >= 1
        assert created_resource.uuid in {r["uuid"] for r in data}

    async def test_manage_project_resource_references(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        created_project_in_personal_ws: Project,
        created_resource: Resource
    ):
        """验证项目可以添加、列出并删除资源引用。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        project_uuid = created_project_in_personal_ws.uuid

        payload = {"resource_uuid": created_resource.uuid, "alias": "primary_resource"}
        response = await client.post(f"/api/v1/projects/{project_uuid}/resources", json=payload, headers=headers)
        assert response.status_code == status.HTTP_201_CREATED, response.text
        created_ref = response.json()["data"]
        assert created_ref["resource_uuid"] == created_resource.uuid
        assert created_ref["alias"] == "primary_resource"

        list_response = await client.get(f"/api/v1/projects/{project_uuid}/resources", headers=headers)
        assert list_response.status_code == status.HTTP_200_OK, list_response.text
        refs = list_response.json()["data"]
        assert created_resource.uuid in {ref["resource_uuid"] for ref in refs}

        delete_response = await client.delete(
            f"/api/v1/projects/{project_uuid}/resources/{created_resource.uuid}", headers=headers
        )
        assert delete_response.status_code == status.HTTP_200_OK, delete_response.text

    async def test_cannot_reference_cross_workspace_resource(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_team: UserContext,
        team_workspace,
    ):
        """验证项目不能引用不同工作空间的资源。"""
        headers = await auth_headers_factory(registered_user_with_team)

        personal_ws_uuid = registered_user_with_team.personal_workspace.uuid
        project_payload = {"name": "Personal Project", "main_application_type": "uiapp"}
        project_response = await client.post(
            f"/api/v1/workspaces/{personal_ws_uuid}/projects",
            json=project_payload,
            headers=headers
        )
        assert project_response.status_code == status.HTTP_201_CREATED, project_response.text
        project_uuid = project_response.json()["data"]["uuid"]

        resource_payload = {"name": "Team Tool", "resource_type": "tool"}
        resource_response = await client.post(
            f"/api/v1/workspaces/{team_workspace.uuid}/resources",
            json=resource_payload,
            headers=headers
        )
        assert resource_response.status_code == status.HTTP_201_CREATED, resource_response.text
        resource_uuid = resource_response.json()["data"]["uuid"]

        ref_payload = {"resource_uuid": resource_uuid}
        ref_response = await client.post(
            f"/api/v1/projects/{project_uuid}/resources",
            json=ref_payload,
            headers=headers
        )
        assert ref_response.status_code == status.HTTP_400_BAD_REQUEST, ref_response.text

class TestGenericResourceMetadata:
    """[通用] 测试对 Resource 逻辑实体（元数据）的通用读写接口。"""

    async def test_get_resource_details_success(self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, created_resource: Resource):
        """[参数化] 成功获取资源的聚合详情，包含工作区实例的内容。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        response = await client.get(f"/api/v1/resources/{created_resource.uuid}", headers=headers)
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["uuid"] == created_resource.uuid
        assert data["workspace_instance"]["uuid"] == created_resource.workspace_instance.uuid
        # 验证 workspace_instance 至少满足通用 InstanceRead 契约
        assert data["workspace_instance"]["name"] == created_resource.name
        assert "version_tag" in data["workspace_instance"]
        assert "status" in data["workspace_instance"]
        assert "created_at" in data["workspace_instance"]
        assert "updated_at" in data["workspace_instance"]
        assert "creator" in data["workspace_instance"]
        
        # 验证特定于类型的字段存在
        if created_resource.resource_type.name == 'tenantdb':
            assert 'tables' in data["workspace_instance"]

    async def test_list_instance_dependencies(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        created_resource: Resource
    ):
        """验证实例依赖解析接口可返回依赖列表。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        instance_uuid = created_resource.workspace_instance.uuid
        response = await client.get(f"/api/v1/instances/{instance_uuid}/dependencies", headers=headers)
        assert response.status_code == status.HTTP_200_OK, response.text
        data = response.json()["data"]
        assert isinstance(data, list)

    async def test_update_resource_metadata_success(self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, created_resource: Resource, db_session: AsyncSession):
        """[参数化] 成功更新Resource元数据，并验证变更已同步到工作区实例。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        payload = {"name": "Updated Name", "description": "Updated desc."}
        response = await client.put(f"/api/v1/resources/{created_resource.uuid}", json=payload, headers=headers)

        assert response.status_code == status.HTTP_200_OK
        
        resource_dao = ResourceDao(db_session)
        updated_resource = await resource_dao.get_one(where={"uuid": created_resource.uuid}, withs=["workspace_instance"])
        assert updated_resource.name == payload["name"]
        assert updated_resource.workspace_instance.name == payload["name"]

class TestGenericInstanceVersioning:
    """[通用] 测试版本管理的核心流程：发布、归档。"""

    async def test_publish_instance_flow(self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, created_resource: Resource, db_session: AsyncSession):
        """[真正通用] 测试完整的发布流程，通过修改通用元数据来创造版本差异。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        workspace_instance_uuid = created_resource.workspace_instance.uuid

        # --- 1. 发布 v1.0.0 ---
        res_v1 = await client.post(f"/api/v1/instances/{workspace_instance_uuid}/publish", json={"version_tag": "1.0.0"}, headers=headers)
        assert res_v1.status_code == status.HTTP_201_CREATED, f"Publish v1.0.0 failed: {res_v1.text}"
        v1_uuid = res_v1.json()["data"]["uuid"]
        
        # --- 2. 修改工作区草稿（通过更新资源元数据）---
        update_payload = {"name": "Version 1.1 Name"}
        await client.put(f"/api/v1/resources/{created_resource.uuid}", json=update_payload, headers=headers)

        # --- 3. 发布 v1.1.0 ---
        res_v2 = await client.post(f"/api/v1/instances/{workspace_instance_uuid}/publish", json={"version_tag": "1.1.0"}, headers=headers)
        assert res_v2.status_code == status.HTTP_201_CREATED, f"Publish v1.1.0 failed: {res_v2.text}"
        v2_uuid = res_v2.json()["data"]["uuid"]

        # --- 4. 验证状态 ---
        resource_dao = ResourceDao(db_session)
        instance_dao = ResourceInstanceDao(db_session)

        # 刷新 resource 以获取最新的 latest_published_instance_id
        final_resource = await resource_dao.get_by_uuid(created_resource.uuid)
        v1_instance = await instance_dao.get_by_uuid(v1_uuid)
        v2_instance = await instance_dao.get_by_uuid(v2_uuid)

        assert final_resource.latest_published_instance_id == v2_instance.id
        assert v2_instance.status == VersionStatus.PUBLISHED
        assert v2_instance.name == "Version 1.1 Name" # 验证内容已更新
        assert v1_instance.status == VersionStatus.PUBLISHED
        assert v1_instance.name != "Version 1.1 Name" # 验证旧版本内容未变

        # --- 5. 归档 v1.1.0 ---
        res_v3 = await client.post(f"/api/v1/instances/{v2_uuid}/archive", json={}, headers=headers)
        assert res_v3.status_code == status.HTTP_200_OK, f"Archive v1.1.0 failed: {res_v3.text}"
        archived_uuid = res_v3.json()["data"]["uuid"]
        archived_instance = await instance_dao.get_by_uuid(archived_uuid)
        assert archived_instance.uuid == v2_uuid
        assert archived_instance.status == VersionStatus.ARCHIVED
        assert archived_instance.resource.latest_published_instance_id == v1_instance.id

class TestGenericDeletionAndPermissions:
    """[通用] 测试删除和权限控制的通用逻辑。"""

    async def test_delete_resource_cascades_to_instances(self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, created_resource: Resource, db_session: AsyncSession):
        """[参数化] 成功删除一个Resource，其所有Instance应被级联删除。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        instance_id_to_check = created_resource.workspace_instance_id
        response = await client.delete(f"/api/v1/resources/{created_resource.uuid}", headers=headers)
        
        assert response.status_code == status.HTTP_200_OK
        assert await ResourceDao(db_session).get_by_uuid(created_resource.uuid) is None
        assert await ResourceInstanceDao(db_session).get_by_pk(instance_id_to_check) is None

    async def test_cannot_delete_active_workspace_instance(self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, created_resource: Resource):
        """[参数化 & 业务规则] 禁止直接删除被用作工作区草稿的Instance。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        response = await client.delete(f"/api/v1/instances/{created_resource.workspace_instance.uuid}", headers=headers)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    async def test_cannot_access_or_modify_others_resource(self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_free: UserContext, created_resource: Resource):
        """[参数化 & 权限] 无关用户不能对他人资源进行任何读写操作。"""
        headers = await auth_headers_factory(registered_user_with_free)
        res_get_resource = await client.get(f"/api/v1/resources/{created_resource.uuid}", headers=headers)
        res_delete_resource = await client.delete(f"/api/v1/resources/{created_resource.uuid}", headers=headers)
        
        assert res_get_resource.status_code == status.HTTP_403_FORBIDDEN
        assert res_delete_resource.status_code == status.HTTP_403_FORBIDDEN


class TestMissingAPIs:
    """[待办清单] 记录在审查中发现的、为完善业务闭环所需的通用资源管理接口。"""

    @pytest.mark.skip(reason="API [GET /resources/{uuid}/versions] not yet implemented.")
    async def test_list_all_versions_of_a_resource(self):
        """
        TODO: 应该有一个接口可以列出某个资源的所有版本历史（包括草稿、已发布、已归档）。
        这对版本回滚、查看历史等功能至关重要。
        """
        # (Arrange) 创建资源并发布多次。
        # (Act) 调用 GET /api/v1/resources/{resource_uuid}/versions
        # (Assert) 响应码为 200 OK，返回包含所有版本摘要信息的列表。
        pass

    @pytest.mark.skip(reason="API [POST /resources/{uuid}/workspace] not yet implemented.")
    async def test_set_active_workspace_instance_from_history(self):
        """
        TODO: 应该有一个接口允许用户将一个历史版本（如一个已归档的版本）
        设置为新的工作区草稿，以实现版本回滚或基于旧版本再创作。
        这将创建一个历史版本的副本作为新的 workspace instance。
        """
        # (Arrange) 创建资源，发布v1，再发布v2，使v1被归档。
        # (Act) 调用 POST /api/v1/resources/{resource_uuid}/workspace，body为 {"source_instance_uuid": "{v1_instance_uuid}"}
        # (Assert) 响应码为 200 OK，返回新的 workspace instance 信息。
        # (Assert) 验证 GET /resources/{resource_uuid}，其 workspace_instance_id 已指向新创建的副本。
        pass

    @pytest.mark.skip(reason="API [POST /projects/{uuid}/fork-resource] not yet implemented.")
    async def test_fork_resource_to_another_project(self):
        """
        TODO: 应该有能力将一个资源（通常是已发布的版本）复制（Fork）到
        自己有权限的另一个项目或工作空间中。
        """
        # (Arrange) userA 创建并发布 resourceA。userB 是另一个用户。
        # (Arrange) userB 创建自己的 projectB。
        # (Act) userB 调用 POST /api/v1/projects/{projectB_uuid}/fork-resource，body为 {"source_instance_uuid": "{resourceA_published_uuid}"}
        # (Assert) 响应码为 201 Created。
        # (Assert) userB 的 projectB 中出现了一个新的 resourceB，其内容与 resourceA 的发布版本相同。
        pass

    @pytest.mark.skip(reason="API for managing resource sharing not yet implemented.")
    async def test_manage_resource_sharing(self):
        """
        TODO: 应该有接口来管理资源的分享设置，例如生成一个只读的公开链接，
        或与特定的用户/团队分享。
        """
        # Example: POST /api/v1/instances/{instance_uuid}/shares
        # Example: GET /api/v1/instances/{instance_uuid}/shares
        pass
