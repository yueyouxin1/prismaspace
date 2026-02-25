# tests/api/v1/test_workspace.py
"""
测试工作空间（Workspace）的全生命周期和权限模型。
此测试套件完全依赖 conftest.py 提供的通用 fixtures。
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import status
import uuid
from typing import Callable

# 导入 ORM 模型用于类型提示和数据库验证
from app.models import User, Team, Workspace, WorkspaceStatus
from app.dao.workspace.workspace_dao import WorkspaceDao

# 导入 conftest.py 中定义的 TestUserContext 数据类
from tests.conftest import UserContext

# 将此模块中的所有测试都标记为异步执行
pytestmark = pytest.mark.asyncio


# ==============================================================================
# 1. 测试套件：读取工作空间 (List & Get)
# ==============================================================================
class TestListAndGetWorkspaces:
    """测试读取工作空间: GET /workspaces 和 GET /workspaces/{uuid}"""

    async def test_list_workspaces_shows_all_accessible(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_team: UserContext,
        team_workspace: Workspace,
    ):
        """[成功路径] 验证用户可以列出自己的个人空间和所属团队的空间。"""
        headers = await auth_headers_factory(registered_user_with_team)
        response = await client.get("/api/v1/workspaces", headers=headers)

        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert len(data) >= 2

        workspace_uuids = {ws["uuid"] for ws in data}
        assert registered_user_with_team.personal_workspace.uuid in workspace_uuids
        assert team_workspace.uuid in workspace_uuids

    async def test_get_personal_workspace_as_owner(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext
    ):
        """[成功路径] 验证用户可以获取自己个人工作空间的详情。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        ws_uuid = registered_user_with_pro.personal_workspace.uuid
        response = await client.get(f"/api/v1/workspaces/{ws_uuid}", headers=headers)

        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["uuid"] == ws_uuid
        assert data["owner"]["uuid"] == registered_user_with_pro.user.uuid
        assert data["owner"]["type"] == "user"

    async def test_get_team_workspace_as_member(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_team: UserContext,
        team_workspace: Workspace,
    ):
        """[成功路径] 验证团队成员可以获取团队工作空间的详情。"""
        headers = await auth_headers_factory(registered_user_with_team)
        response = await client.get(f"/api/v1/workspaces/{team_workspace.uuid}", headers=headers)

        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["uuid"] == team_workspace.uuid
        assert data["owner"]["uuid"] == team_workspace.team.uuid
        assert data["owner"]["type"] == "team"

    async def test_get_others_personal_workspace_fails(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, registered_user_with_free: UserContext
    ):
        """[失败路径] 验证用户不能获取他人个人工作空间的详情。"""
        # another_user 尝试访问 user 的空间
        headers = await auth_headers_factory(registered_user_with_free)
        ws_uuid_to_access = registered_user_with_pro.personal_workspace.uuid

        response = await client.get(f"/api/v1/workspaces/{ws_uuid_to_access}", headers=headers)
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ==============================================================================
# 2. 测试套件：创建工作空间 (Create)
# ==============================================================================
class TestCreateWorkspace:
    """测试创建工作空间: POST /workspaces"""

    async def test_create_team_workspace_success(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_team: UserContext, created_team: Team
    ):
        """[成功路径] 验证团队成员可以为团队成功创建一个工作空间。"""
        headers = await auth_headers_factory(registered_user_with_team)
        payload = {"name": "New Research Workspace", "owner_team_uuid": created_team.uuid}

        response = await client.post("/api/v1/workspaces", json=payload, headers=headers)

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()["data"]
        assert data["name"] == payload["name"]
        assert data["owner"]["uuid"] == created_team.uuid
        assert data["owner"]["type"] == "team"

    async def test_create_workspace_for_non_existent_team_fails(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_team: UserContext
    ):
        """[健壮性测试] 尝试为一个不存在的团队创建工作空间，应返回 404。"""
        headers = await auth_headers_factory(registered_user_with_team)
        non_existent_team_uuid = str(uuid.uuid4())
        payload = {"name": "Ghost Workspace", "owner_team_uuid": non_existent_team_uuid}

        response = await client.post("/api/v1/workspaces", json=payload, headers=headers)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_create_workspace_for_non_member_team_fails(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, created_team: Team
    ):
        """[失败路径] 验证非团队成员尝试为团队创建工作空间会被禁止。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        payload = {"name": "Intruder Workspace", "owner_team_uuid": created_team.uuid}

        response = await client.post("/api/v1/workspaces", json=payload, headers=headers)
        assert response.status_code == status.HTTP_403_FORBIDDEN


# ==============================================================================
# 3. 测试套件：更新工作空间 (Update)
# ==============================================================================
class TestUpdateWorkspace:
    """测试更新工作空间: PUT /workspaces/{uuid}"""

    async def test_update_personal_workspace_success(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext
    ):
        """[成功路径] 验证用户可以成功更新自己的个人工作空间。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        ws_uuid = registered_user_with_pro.personal_workspace.uuid
        payload = {"name": "My Updated Personal Space", "avatar": "https://example.com/me.png"}

        response = await client.put(f"/api/v1/workspaces/{ws_uuid}", json=payload, headers=headers)

        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["name"] == payload["name"]
        assert data["avatar"] == payload["avatar"]

    async def test_update_team_workspace_as_owner(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_team: UserContext,
        team_workspace: Workspace,
    ):
        """[成功路径] 验证团队所有者可以成功更新团队工作空间。"""
        headers = await auth_headers_factory(registered_user_with_team)
        payload = {"name": "Updated Shared Workspace"}

        response = await client.put(f"/api/v1/workspaces/{team_workspace.uuid}", json=payload, headers=headers)
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["data"]["name"] == payload["name"]


# ==============================================================================
# 4. 测试套件：归档工作空间 (Archive/Delete)
# ==============================================================================
class TestArchiveWorkspace:
    """测试归档工作空间: DELETE /workspaces/{uuid}"""

    async def test_archive_team_workspace_success(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_team: UserContext,
        team_workspace: Workspace,
        db_session: AsyncSession,
    ):
        """[成功路径] 验证团队所有者可以成功归档团队工作空间。"""
        headers = await auth_headers_factory(registered_user_with_team)
        response = await client.delete(f"/api/v1/workspaces/{team_workspace.uuid}", headers=headers)

        assert response.status_code == status.HTTP_200_OK

        # 验证数据库状态
        archived_ws = await WorkspaceDao(db_session).get_by_pk(team_workspace.id)
        assert archived_ws.status == WorkspaceStatus.ARCHIVED

    async def test_archive_personal_workspace_fails(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext
    ):
        """[失败路径] 验证归档个人工作空间会返回预期的业务错误。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        ws_uuid = registered_user_with_pro.personal_workspace.uuid

        response = await client.delete(f"/api/v1/workspaces/{ws_uuid}", headers=headers)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Personal workspace cannot be archived" in response.json()["msg"]


# ==============================================================================
# 5. 待办测试清单 (Identified Missing Endpoints & Scenarios)
# ==============================================================================
@pytest.mark.skip(reason="Requires full invitation flow and multiple team roles to be testable via API.")
class TestTeamMemberWorkspacePermissions:
    """
    [TODO] 这是一个待办的测试套件，用于验证不同角色的团队成员对工作空间的权限。
    需要前置条件：
    1. 完整的团队邀请 API (POST /teams/{uuid}/invitations, POST /invitations/accept)
    2. 完整的团队成员角色修改 API (PUT /teams/{uuid}/members/{member_uuid})
    """

    async def test_team_admin_can_create_workspace(self):
        """[TODO] 验证团队管理员（非所有者）可以创建工作空间。"""
        pass

    async def test_regular_member_cannot_create_workspace(self):
        """[TODO] 验证普通团队成员不能创建工作空间。"""
        pass

    async def test_team_admin_can_update_workspace(self):
        """[TODO] 验证团队管理员可以更新工作空间设置。"""
        pass

    async def test_regular_member_cannot_update_workspace(self):
        """[TODO] 验证普通团队成员不能更新工作空间设置。"""
        pass
    
    async def test_team_admin_cannot_archive_workspace(self):
        """[TODO] 验证团队管理员（非所有者）不能归档工作空间。"""
        pass


@pytest.mark.skip(reason="Feature/API endpoint not yet implemented.")
class TestUnarchiveWorkspace:
    """[TODO] 测试恢复已归档的工作空间"""
    
    async def test_unarchive_workspace_success(self):
        """
        [TODO] 测试恢复一个已归档的工作空间。
        - 预计 API: POST /workspaces/{uuid}/unarchive
        - 流程:
          1. 创建并归档一个团队工作空间。
          2. 调用 unarchive API。
          3. 验证 HTTP 响应为 200 OK。
          4. 从数据库中验证工作空间的状态已变回 'active'。
        """
        pass