# tests/api/v1/test_identity.py
#
# 本测试文件专注于身份认证与用户管理的核心API。
# 它完全依赖于 conftest.py 中定义的通用 fixtures，如 client, db_session, 
# 以及 registered_user_with_pro, auth_headers_factory 等，以保持测试代码的整洁和聚焦。

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import status
import uuid

# --- 从 conftest.py 导入核心 fixtures ---
# UserContext 是一个数据类，封装了 user ORM 对象和其密码
from tests.conftest import UserContext 
from app.models import User
from app.dao.identity.user_dao import UserDao
from app.core.security import verify_password

# 将此模块中的所有测试都标记为异步执行
pytestmark = pytest.mark.asyncio

# ==============================================================================
# 1. 测试套件
# ==============================================================================

class TestRegistration:
    """测试用户注册接口: POST /api/v1/identity/register"""

    async def test_register_user_success(self, client: AsyncClient, db_session: AsyncSession, user_data_factory):
        """[成功路径] 验证用户可以使用有效的邮箱和密码成功注册。"""
        # Arrange: 使用 factory 生成唯一的测试数据
        user_data = user_data_factory()

        # Act
        response = await client.post("/api/v1/identity/register", json=user_data)
        
        # Assert: 1. 验证HTTP响应
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()["data"]
        assert data["email"] == user_data["email"]
        assert "uuid" in data
        assert "password_hash" not in data  # 确保敏感信息没有被返回

        # Assert: 2. 验证数据库状态
        user_in_db = await UserDao(db_session).get_by_email(user_data["email"])
        assert user_in_db is not None
        assert user_in_db.email == user_data["email"]
        assert verify_password(user_data["password"], user_in_db.password_hash)

    async def test_register_user_duplicate_email(self, client: AsyncClient, registered_user_with_pro: UserContext):
        """[失败路径] 验证使用已存在的邮箱注册会返回冲突错误。"""
        # Arrange: `registered_user_with_pro` fixture 已为我们创建了一个用户
        duplicate_data = {"email": registered_user_with_pro.user.email, "password": "another-password"}

        # Act
        response = await client.post("/api/v1/identity/register", json=duplicate_data)
        
        # Assert
        assert response.status_code == status.HTTP_409_CONFLICT
        assert "Email already registered" in response.json()["msg"]

    @pytest.mark.parametrize("invalid_payload, expected_status", [
        ({"email": "not-an-email", "password": "a-secure-password-123"}, status.HTTP_422_UNPROCESSABLE_CONTENT),
        ({"email": f"shortpass_{uuid.uuid4()}@example.com", "password": "123"}, status.HTTP_422_UNPROCESSABLE_CONTENT),
        ({}, status.HTTP_422_UNPROCESSABLE_CONTENT),
    ])
    async def test_register_user_invalid_input(self, client: AsyncClient, invalid_payload: dict, expected_status: int):
        """[失败路径] 使用参数化测试所有因输入不合法导致的失败场景。"""
        response = await client.post("/api/v1/identity/register", json=invalid_payload)
        assert response.status_code == expected_status

class TestLogin:
    """测试用户认证接口: POST /api/v1/identity/token"""

    async def test_login_success(self, client: AsyncClient, registered_user_with_pro: UserContext):
        """[成功路径] 验证已注册用户可以使用正确的凭证成功登录并获取token。"""
        # Arrange: 从 registered_user_with_pro 获取登录所需信息
        login_data = {
            "grant_type": "password",
            "identifier": registered_user_with_pro.user.email,
            "password": registered_user_with_pro.password,
        }
        
        # Act
        response = await client.post("/api/v1/identity/token", json=login_data)
        
        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    @pytest.mark.parametrize("identifier_key, password_key", [
        ("email", "wrong_password"),  # 密码错误
        ("non_existent_email", "password"),   # 用户不存在
    ])
    async def test_login_failure(self, client: AsyncClient, registered_user_with_pro: UserContext, identifier_key, password_key):
        """[失败路径] 统一测试所有因凭证无效导致的登录失败场景。"""
        identifier = registered_user_with_pro.user.email if identifier_key == "email" else "nonexistent@example.com"
        password = "this-is-wrong" if password_key == "wrong_password" else registered_user_with_pro.password
        
        login_data = {"grant_type": "password", "identifier": identifier, "password": password}
        
        response = await client.post("/api/v1/identity/token", json=login_data)
        
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Incorrect identifier or password" in response.json()["msg"]


class TestCurrentUser:
    """测试当前用户信息接口: GET /api/v1/identity/users/me"""

    async def test_get_current_user_success(self, client: AsyncClient, registered_user_with_pro: UserContext, auth_headers_factory):
        """[成功路径] 验证使用有效的token可以成功获取当前用户信息。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        response = await client.get("/api/v1/identity/users/me", headers=headers)
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["email"] == registered_user_with_pro.user.email
        assert data["uuid"] == registered_user_with_pro.user.uuid

    @pytest.mark.parametrize("headers", [
        {}, 
        {"Authorization": "Bearer this.is.an.invalid.token"},
    ])
    async def test_get_current_user_unauthorized(self, client: AsyncClient, headers: dict):
        """[失败路径] 统一测试所有因token无效或缺失导致的未授权访问。"""
        response = await client.get("/api/v1/identity/users/me", headers=headers)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

# ==============================================================================
# 2. 待办测试 (发现缺失的API接口)
# ==============================================================================

@pytest.mark.skip(reason="API endpoint PUT /api/v1/identity/users/me is not yet implemented.")
class TestUserProfile:
    """测试用户资料管理接口"""

    async def test_update_profile_success(self, client: AsyncClient, registered_user_with_pro: UserContext, auth_headers_factory, db_session: AsyncSession):
        """[待办] 验证用户可以成功更新自己的昵称和头像。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        payload = {"nick_name": "Updated Name", "avatar": "https://example.com/new.png"}
        
        response = await client.put("/api/v1/identity/users/me", json=payload, headers=headers)
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["nick_name"] == "Updated Name"
        
        await db_session.refresh(registered_user_with_pro.user)
        assert registered_user_with_pro.user.nick_name == "Updated Name"

    async def test_cannot_update_others_profile(self, client: AsyncClient, registered_user_with_pro: UserContext, registered_user_with_free: UserContext, auth_headers_factory):
        """[待办][权限] 验证用户不能更新他人的个人资料。"""
        # 使用 another_user 的 token 尝试更新 user 的资料 (这是不可能的，因为只能更新 'me')
        headers = await auth_headers_factory(registered_user_with_free)
        payload = {"nick_name": "Hijacked Name"}
        
        # 即使API是 /users/me, 我们也应该确认其内部逻辑是基于token主体的，而非任何body参数
        response = await client.put("/api/v1/identity/users/me", json=payload, headers=headers)
        
        assert response.status_code == status.HTTP_200_OK
        # 验证被更新的是 another_user, 而非 registered_user_with_pro.user
        assert response.json()["data"]["nick_name"] == "Hijacked Name"
        
        # 确认 registered_user_with_pro.user 的昵称没有被改变
        get_me_headers = await auth_headers_factory(registered_user_with_pro)
        me_response = await client.get("/api/v1/identity/users/me", headers=get_me_headers)
        assert me_response.json()["data"]["nick_name"] != "Hijacked Name"

@pytest.mark.skip(reason="API endpoint PUT /api/v1/identity/users/me/password is not yet implemented.")
class TestPasswordChange:
    """[补充] 测试密码修改功能，这是一个关键的安全接口。"""

    async def test_change_password_success(self, client: AsyncClient, registered_user_with_pro: UserContext, auth_headers_factory):
        """[待办] 验证用户使用正确的旧密码可以成功修改密码。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        new_password = f"new-strong-password-{uuid.uuid4()}"
        payload = {
            "old_password": registered_user_with_pro.password,
            "new_password": new_password
        }
        
        response = await client.put("/api/v1/identity/users/me/password", json=payload, headers=headers)
        assert response.status_code == status.HTTP_204_NO_CONTENT

        # 验证新密码可以用于登录
        login_data = {"grant_type": "password", "identifier": registered_user_with_pro.user.email, "password": new_password}
        login_response = await client.post("/api/v1/identity/token", json=login_data)
        assert login_response.status_code == status.HTTP_200_OK

    async def test_change_password_with_wrong_old_password(self, client: AsyncClient, registered_user_with_pro: UserContext, auth_headers_factory):
        """[待办] 验证使用错误的旧密码修改密码会失败。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        payload = {
            "old_password": "this-is-incorrect",
            "new_password": "a-new-password"
        }
        
        response = await client.put("/api/v1/identity/users/me/password", json=payload, headers=headers)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.skip(reason="API endpoints for user API key management are not yet implemented.")
class TestUserApiKeys:
    """[补充] 测试用户个人API密钥管理的全生命周期和权限。"""

    async def test_api_key_lifecycle(self, client: AsyncClient, registered_user_with_pro: UserContext, auth_headers_factory):
        """[待办] 验证用户可以创建、列出和删除自己的API密钥。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        
        # 1. Create
        create_response = await client.post("/api/v1/identity/users/me/api-keys", json={"description": "My test key"}, headers=headers)
        assert create_response.status_code == status.HTTP_201_CREATED
        key_data = create_response.json()["data"]
        assert "api_key" in key_data and key_data["api_key"].startswith("sk-")
        assert "prefix" in key_data
        
        # 2. List
        list_response = await client.get("/api/v1/identity/users/me/api-keys", headers=headers)
        assert list_response.status_code == status.HTTP_200_OK
        keys_list = list_response.json()["data"]
        assert len(keys_list) == 1
        assert keys_list[0]["prefix"] == key_data["prefix"]
        assert "api_key" not in keys_list[0]
        
        # 3. Revoke
        key_prefix_to_delete = key_data["prefix"]
        delete_response = await client.delete(f"/api/v1/identity/users/me/api-keys/{key_prefix_to_delete}", headers=headers)
        assert delete_response.status_code == status.HTTP_204_NO_CONTENT
        
        # 4. Verify Deletion
        list_after_delete = await client.get("/api/v1/identity/users/me/api-keys", headers=headers)
        assert len(list_after_delete.json()["data"]) == 0

    async def test_cannot_list_or_delete_others_api_keys(self, client: AsyncClient, registered_user_with_pro: UserContext, registered_user_with_free: UserContext, auth_headers_factory):
        """[待办][权限] 验证用户无法查看或删除其他用户的API密钥。"""
        # User 1 creates a key
        user1_headers = await auth_headers_factory(registered_user_with_pro)
        create_resp = await client.post("/api/v1/identity/users/me/api-keys", json={"description": "User 1 Key"}, headers=user1_headers)
        key_prefix = create_resp.json()["data"]["prefix"]

        # User 2 tries to list User 1's keys (impossible as API is scoped to 'me')
        user2_headers = await auth_headers_factory(registered_user_with_free)
        list_resp = await client.get("/api/v1/identity/users/me/api-keys", headers=user2_headers)
        assert list_resp.status_code == status.HTTP_200_OK
        assert len(list_resp.json()["data"]) == 0 # User 2 sees their own empty list

        # User 2 tries to delete User 1's key by guessing the prefix
        delete_resp = await client.delete(f"/api/v1/identity/users/me/api-keys/{key_prefix}", headers=user2_headers)
        assert delete_resp.status_code == status.HTTP_404_NOT_FOUND # Should not find the key under their own scope