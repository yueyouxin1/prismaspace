# tests/api/v1/test_team.py

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import status
from typing import Callable

from app.models import User, Team, TeamMember
from app.dao.identity.team_dao import TeamDao
from tests.conftest import UserContext # 导入 UserContext 以获得类型提示

# 将此模块中的所有测试都标记为异步执行
pytestmark = pytest.mark.asyncio


class TestTeamLifecycle:
    """测试团队的创建、读取、更新、删除 (CRUD) 核心生命周期。"""

    async def test_create_team_success(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_team: UserContext
    ):
        """[成功路径] 验证有权限的用户可以成功创建团队。"""
        headers = await auth_headers_factory(registered_user_with_team)
        payload = {"name": "Test Team Bravo", "avatar": "https://example.com/bravo.png"}
        
        response = await client.post("/api/v1/teams", json=payload, headers=headers)
        
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()["data"]
        assert data["name"] == payload["name"]
        assert data["avatar"] == payload["avatar"]

    async def test_create_team_without_permission_fails(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext
    ):
        """[失败路径] 验证没有 'team:create' 权限的用户 (默认Pro/Free Plan) 无法创建团队。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        payload = {"name": "Forbidden Team"}
        
        response = await client.post("/api/v1/teams", json=payload, headers=headers)
        
        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_update_team_as_owner(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_team: UserContext, created_team: Team
    ):
        """[成功路径] 验证团队所有者可以更新团队信息。"""
        headers = await auth_headers_factory(registered_user_with_team)
        payload = {"name": "Updated Test Team Alpha", "avatar": "https://example.com/new_avatar.png"}
        
        response = await client.put(f"/api/v1/teams/{created_team.uuid}", json=payload, headers=headers)
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["name"] == payload["name"]
        assert data["avatar"] == payload["avatar"]

    async def test_delete_team_as_owner(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_team: UserContext, created_team: Team
    ):
        """[成功路径] 验证团队所有者可以删除团队。"""
        headers = await auth_headers_factory(registered_user_with_team)
        response = await client.delete(f"/api/v1/teams/{created_team.uuid}", headers=headers)
        
        assert response.status_code == status.HTTP_200_OK

        # 验证团队是否真的被从数据库中删除了
        response_get = await client.get(f"/api/v1/teams/{created_team.uuid}", headers=headers)
        assert response_get.status_code == status.HTTP_404_NOT_FOUND


class TestTeamAccessPermissions:
    """测试对团队资源的访问权限控制，确保非成员无法访问。"""
    
    async def test_get_team_as_owner(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_team: UserContext, created_team: Team
    ):
        """[成功路径] 团队所有者可以获取团队信息。"""
        headers = await auth_headers_factory(registered_user_with_team)
        response = await client.get(f"/api/v1/teams/{created_team.uuid}", headers=headers)
        
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["data"]["uuid"] == created_team.uuid

    async def test_non_member_cannot_get_team(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_free: UserContext, created_team: Team
    ):
        """[失败路径] 非团队成员获取团队信息，应该被禁止。"""
        headers = await auth_headers_factory(registered_user_with_free)
        response = await client.get(f"/api/v1/teams/{created_team.uuid}", headers=headers)
        
        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_non_member_cannot_update_team(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_free: UserContext, created_team: Team
    ):
        """[失败路径] 非团队成员尝试更新团队信息，应该被禁止。"""
        headers = await auth_headers_factory(registered_user_with_free)
        payload = {"name": "Hijacked Name"}
        response = await client.put(f"/api/v1/teams/{created_team.uuid}", json=payload, headers=headers)
        
        assert response.status_code == status.HTTP_403_FORBIDDEN
        
    @pytest.mark.skip(reason="TODO: 需先实现邀请流程，以创建非所有者的 'team:admin' 角色进行测试。")
    async def test_non_owner_admin_cannot_delete_team(self, client: AsyncClient, auth_headers_factory: Callable, created_team: Team):
        """[待办-失败路径] 验证即使是团队管理员（非所有者），也无法删除团队。"""
        # 伪代码:
        # 1. (Arrange) 邀请一个新用户 new_admin_context 作为 'team:admin' 加入 created_team 并接受。
        # 2. (Arrange) 使用 new_admin_context 获取认证头。
        # 3. (Act) 尝试 DELETE /api/v1/teams/{created_team.uuid}
        # 4. (Assert) 响应码应为 403 Forbidden。
        pass


class TestTeamMemberManagement:
    """测试团队成员的列出、移除等管理功能。"""

    async def test_list_members_as_owner(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_team: UserContext, created_team: Team
    ):
        """[成功路径] 验证所有者可以列出团队成员（初始时只有自己）。"""
        headers = await auth_headers_factory(registered_user_with_team)
        response = await client.get(f"/api/v1/teams/{created_team.uuid}/members", headers=headers)
        
        assert response.status_code == status.HTTP_200_OK
        members = response.json()["data"]
        assert len(members) == 1
        
        owner_member = members[0]
        assert owner_member["user"]["uuid"] == registered_user_with_team.user.uuid
        assert owner_member["role"]["name"] == "team:owner"
        assert "uuid" in owner_member  # 验证成员关系本身的uuid存在

    async def test_remove_owner_fails(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_team: UserContext, created_team: Team
    ):
        """[失败路径-业务规则] 验证不能通过API移除团队所有者。"""
        headers = await auth_headers_factory(registered_user_with_team)

        # Arrange: 首先获取所有者自己的成员关系UUID
        list_response = await client.get(f"/api/v1/teams/{created_team.uuid}/members", headers=headers)
        owner_member_uuid = list_response.json()["data"][0]["uuid"]

        # Act: 尝试通过API删除所有者
        delete_response = await client.delete(f"/api/v1/teams/{created_team.uuid}/members/{owner_member_uuid}", headers=headers)

        # Assert
        assert delete_response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Cannot remove the team owner" in delete_response.json()["msg"]
    
    async def test_cannot_remove_self_from_team(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_team: UserContext, created_team: Team
    ):
        """[失败路径-业务规则] 验证用户不能用移除接口来移除自己（应有专门的'离开团队'接口）。"""
        headers = await auth_headers_factory(registered_user_with_team)
        list_response = await client.get(f"/api/v1/teams/{created_team.uuid}/members", headers=headers)
        owner_member_uuid = list_response.json()["data"][0]["uuid"]

        delete_response = await client.delete(f"/api/v1/teams/{created_team.uuid}/members/{owner_member_uuid}", headers=headers)
        
        assert delete_response.status_code == status.HTTP_400_BAD_REQUEST
        # 错误信息可能是 "Cannot remove the team owner" 或 "You cannot remove yourself..."，取决于哪个检查在前。
        # 只要是400业务错误即可。
        assert "remove" in delete_response.json()["msg"]


@pytest.mark.skip(reason="TODO: 核心邀请流程API (POST /teams/{uuid}/invitations, POST /invitations/accept) 尚未实现。")
class TestTeamInvitationLifecycle:
    """[待办] 测试完整的成员邀请、接受、管理和移除的生命周期。"""

    async def test_full_invitation_and_removal_flow(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_team: UserContext, registered_user_with_free: UserContext, created_team: Team
    ):
        """验证一个完整的成员管理生命周期：邀请 -> 接受 -> 列出 -> 移除。"""
        owner_headers = await auth_headers_factory(registered_user_with_team)
        
        # === 1. 邀请成员 (Invite) ===
        # 缺失API: POST /api/v1/teams/{team_uuid}/invitations
        invite_payload = {"target_identifier": registered_user_with_free.user.email, "role_name": "team:member"}
        invite_response = await client.post(f"/api/v1/teams/{created_team.uuid}/invitations", json=invite_payload, headers=owner_headers)
        assert invite_response.status_code == status.HTTP_201_CREATED
        invitation_token = invite_response.json()["data"]["token"]
        
        # === 2. 接受邀请 (Accept) ===
        # 缺失API: POST /api/v1/invitations/accept
        invited_user_headers = await auth_headers_factory(registered_user_with_free)
        accept_payload = {"token": invitation_token}
        accept_response = await client.post("/api/v1/invitations/accept", json=accept_payload, headers=invited_user_headers)
        assert accept_response.status_code == status.HTTP_200_OK
        
        # === 3. 验证成员列表 (Verify) ===
        list_response = await client.get(f"/api/v1/teams/{created_team.uuid}/members", headers=owner_headers)
        members = list_response.json()["data"]
        assert len(members) == 2
        member_uuids = {m["user"]["uuid"] for m in members}
        assert registered_user_with_free.user.uuid in member_uuids
        
        # 找到新成员的成员关系UUID
        new_member_entry = next(m for m in members if m["user"]["uuid"] == registered_user_with_free.user.uuid)
        new_member_relation_uuid = new_member_entry["uuid"]
        
        # === 4. 移除成员 (Remove) ===
        remove_response = await client.delete(f"/api/v1/teams/{created_team.uuid}/members/{new_member_relation_uuid}", headers=owner_headers)
        assert remove_response.status_code == status.HTTP_204_NO_CONTENT
        
        # === 5. 再次验证 (Final Verify) ===
        final_list_response = await client.get(f"/api/v1/teams/{created_team.uuid}/members", headers=owner_headers)
        assert len(final_list_response.json()["data"]) == 1

    async def test_non_admin_cannot_invite_member(self):
        """[待办-失败路径] 验证普通成员（非Admin/Owner）无法邀请新成员。"""
        pass

    async def test_list_pending_invitations(self):
        """[待办-成功路径] 验证团队管理员可以查看待处理的邀请列表。"""
        # 缺失API: GET /api/v1/teams/{team_uuid}/invitations
        pass

    async def test_cancel_invitation(self):
        """[待办-成功路径] 验证团队管理员可以取消一个已发送但未被接受的邀请。"""
        # 缺失API: DELETE /api/v1/teams/{team_uuid}/invitations/{invitation_uuid}
        pass