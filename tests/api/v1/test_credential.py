# tests/api/v1/test_credential.py

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import status
from typing import Callable, Dict

# --- 从 conftest 导入通用 Fixtures ---
from tests.conftest import (
    registered_user_with_pro,
    registered_user_with_free,
    created_team,
    team_workspace,
    registered_user_with_team,
    UserContext
)

from app.models import Workspace
from app.core.encryption import decrypt # 导入解密函数用于验证
from app.dao.module.service_module_credential_dao import ServiceModuleCredentialDao
from app.dao.module.service_module_dao import ServiceModuleProviderDao

# 将所有测试标记为异步
pytestmark = pytest.mark.asyncio


# ==============================================================================
# 1. 核心 Fixtures (本文件特有)
# ==============================================================================

@pytest.fixture
async def created_credential_in_personal_ws(
    client: AsyncClient, 
    auth_headers_factory: Callable,
    registered_user_with_pro: UserContext,
    credential_payload_factory: Callable
) -> Dict:
    """
    通过 API 在用户的个人工作空间中创建一个凭证，并返回其 API 响应数据。
    """
    headers = await auth_headers_factory(registered_user_with_pro)
    workspace_uuid = registered_user_with_pro.personal_workspace.uuid
    payload = await credential_payload_factory()
    
    response = await client.post(f"/api/v1/workspaces/{workspace_uuid}/credentials/service-modules", json=payload, headers=headers)
    assert response.status_code == status.HTTP_201_CREATED, "Fixture setup failed: Credential creation failed"
    
    # 附加 workspace_uuid 以便后续测试使用
    response_data = response.json()["data"]
    response_data["workspace_uuid"] = workspace_uuid
    return response_data

# ==============================================================================
# 2. 测试套件
# ==============================================================================

class TestCredentialLifecycle:
    """测试凭证在授权的工作空间中的完整生命周期。"""

    async def test_create_credential_success(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, 
        credential_payload_factory: Callable, db_session: AsyncSession
    ):
        """[成功路径] 验证用户可以在自己的工作空间中成功创建凭证。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        workspace_uuid = registered_user_with_pro.personal_workspace.uuid
        payload = await credential_payload_factory(provider_name="openai", value="ak-test-anthropic-key")
        
        response = await client.post(f"/api/v1/workspaces/{workspace_uuid}/credentials/service-modules", json=payload, headers=headers)
        
        # 1. Assert API Response
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()["data"]
        assert data["provider_id"] == payload["provider_id"]
        assert data["label"] == "My OpenAI Key" # label from factory
        assert "value" not in data # 确保敏感信息绝不返回

        # 2. Assert Database State
        cred_dao = ServiceModuleCredentialDao(db_session)
        db_cred = await cred_dao.get_by_uuid(data["uuid"], withs=['provider'])
        assert db_cred is not None
        assert db_cred.provider.name == "openai"
        # 验证加密
        assert decrypt(db_cred.encrypted_value) == "ak-test-anthropic-key"

    async def test_create_duplicate_credential_fails(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext,
        created_credential_in_personal_ws: Dict, credential_payload_factory: Callable
    ):
        """[业务规则] 在同一个工作空间和提供商下创建重复的凭证应该失败。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        workspace_uuid = created_credential_in_personal_ws["workspace_uuid"]
        # 使用相同的 provider ('openai')
        payload = await credential_payload_factory(provider_name="openai", value="sk-another-key")

        response = await client.post(f"/api/v1/workspaces/{workspace_uuid}/credentials/service-modules", json=payload, headers=headers)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "already exists in this workspace" in response.json()["msg"]
        
    async def test_list_credentials_in_workspace(
        self, client: AsyncClient, db_session: AsyncSession, auth_headers_factory: Callable, registered_user_with_pro: UserContext,
        created_credential_in_personal_ws: Dict
    ):
        """[成功路径] 验证用户可以列出其工作空间中的凭证。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        workspace_uuid = created_credential_in_personal_ws["workspace_uuid"]
        
        response = await client.get(f"/api/v1/workspaces/{workspace_uuid}/credentials/service-modules", headers=headers)
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert isinstance(data, list)
        assert len(data) == 1
        
        cred_info = data[0]
        assert cred_info["uuid"] == created_credential_in_personal_ws["uuid"]
        provider_dao = ServiceModuleProviderDao(db_session)
        provider = await provider_dao.get_by_name("openai")
        assert cred_info["provider_id"] == provider.id

    async def test_update_credential_success(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext,
        created_credential_in_personal_ws: Dict, db_session: AsyncSession
    ):
        """[成功路径] 验证用户可以更新凭证的标签和值。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        workspace_uuid = created_credential_in_personal_ws["workspace_uuid"]
        cred_uuid = created_credential_in_personal_ws["uuid"]
        payload = {"label": "Updated Personal OpenAI Key", "value": "sk-new-value-updated"}
        
        response = await client.put(f"/api/v1/workspaces/{workspace_uuid}/credentials/service-modules/{cred_uuid}", json=payload, headers=headers)
        
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["data"]["label"] == payload["label"]

        # 验证数据库中的值已被更新和加密
        cred_dao = ServiceModuleCredentialDao(db_session)
        db_cred = await cred_dao.get_by_uuid(cred_uuid)
        assert decrypt(db_cred.encrypted_value) == payload["value"]

    async def test_delete_credential_success(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext,
        created_credential_in_personal_ws: Dict, db_session: AsyncSession
    ):
        """[成功路径] 验证用户可以删除凭证。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        workspace_uuid = created_credential_in_personal_ws["workspace_uuid"]
        cred_uuid = created_credential_in_personal_ws["uuid"]

        response = await client.delete(f"/api/v1/workspaces/{workspace_uuid}/credentials/service-modules/{cred_uuid}", headers=headers)
        
        assert response.status_code == status.HTTP_200_OK

        # 验证数据库中已删除
        cred_dao = ServiceModuleCredentialDao(db_session)
        assert await cred_dao.get_by_uuid(cred_uuid) is None


class TestCredentialPermissions:
    """测试对凭证资源的权限隔离和访问控制。"""

    async def test_another_user_cannot_list_credentials(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_free: UserContext,
        created_credential_in_personal_ws: Dict
    ):
        """[权限隔离] 无关用户不能列出他人工作空间中的凭证。"""
        headers = await auth_headers_factory(registered_user_with_free)
        workspace_uuid_to_invade = created_credential_in_personal_ws["workspace_uuid"]
        
        response = await client.get(f"/api/v1/workspaces/{workspace_uuid_to_invade}/credentials/service-modules", headers=headers)
        
        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_another_user_cannot_create_credential(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_free: UserContext,
        registered_user_with_pro: UserContext, credential_payload_factory: Callable
    ):
        """[权限隔离] 无关用户不能在他人工作空间中创建凭证。"""
        headers = await auth_headers_factory(registered_user_with_free)
        workspace_uuid_to_invade = registered_user_with_pro.personal_workspace.uuid
        payload = await credential_payload_factory()

        response = await client.post(f"/api/v1/workspaces/{workspace_uuid_to_invade}/credentials/service-modules", json=payload, headers=headers)
        
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.usefixtures("created_credential_in_personal_ws")
    @pytest.mark.parametrize("http_method, url_template", [
        ("PUT", "/api/v1/workspaces/{ws_uuid}/credentials/service-modules/{cred_uuid}"),
        ("DELETE", "/api/v1/workspaces/{ws_uuid}/credentials/service-modules/{cred_uuid}"),
    ])
    async def test_another_user_cannot_modify_credentials(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_free: UserContext,
        created_credential_in_personal_ws: Dict, http_method: str, url_template: str
    ):
        """[权限隔离] 无关用户不能更新或删除他人工作空间中的凭证。"""
        headers = await auth_headers_factory(registered_user_with_free)
        url = url_template.format(
            ws_uuid=created_credential_in_personal_ws["workspace_uuid"],
            cred_uuid=created_credential_in_personal_ws["uuid"]
        )
        
        if http_method == "PUT":
            response = await client.put(url, json={"label": "Hacked Label"}, headers=headers)
        else:
            response = await client.delete(url, headers=headers)
            
        assert response.status_code == status.HTTP_403_FORBIDDEN
        
    @pytest.mark.skip(reason="Requires team member roles to be fully implemented and testable via API.")
    async def test_team_member_with_read_only_role_cannot_create(
        self, client: AsyncClient, auth_headers_factory: Callable, team_workspace: Workspace,
        credential_payload_factory: Callable
    ):
        """[角色权限] 验证一个只有只读权限的团队成员无法创建凭证。"""
        # (Arrange) 邀请一个新用户 new_member, 并赋予其一个自定义的、不包含
        #           'workspace:credential:servicemodule:create' 权限的角色。
        # (Arrange) new_member 接受邀请。
        # (Act) new_member 尝试在 team_workspace 中创建凭证。
        # (Assert) 响应码应为 403 Forbidden。
        pass


class TestMissingCredentialAPIs:
    """[待办清单] 记录在审查中发现的、为完善凭证管理业务闭环所需的接口。"""
    
    @pytest.mark.skip(reason="API [GET /credentials/supported-providers] not yet implemented.")
    async def test_list_supported_providers(self, client: AsyncClient):
        """
        TODO: 应该有一个公开的、无需认证的接口，用于告诉前端当前平台支持哪些
        服务提供商的“自带密钥”(BYOK)模式。
        这将使前端能够动态地渲染凭证创建表单的下拉选项。
        """
        # (Act) 调用 GET /api/v1/credentials/supported-providers
        # (Assert) 响应码为 200 OK
        # (Assert) 响应体应为一个列表，例如:
        #          [{"name": "openai", "label": "OpenAI"}, {"name": "anthropic", "label": "Anthropic"}]
        pass

    @pytest.mark.skip(reason="API [POST /workspaces/{uuid}/credentials/service-modules/{cred_uuid}/test] not yet implemented.")
    async def test_test_credential_validity(self):
        """
        TODO: 在用户保存一个凭证后，应该有一个“测试连接”的按钮。
        这个接口会使用用户提供的凭证，尝试向对应的服务提供商（如OpenAI）
        发起一个简单、低成本的API调用（例如，列出模型列表），以验证密钥的有效性。
        """
        # (Arrange) 创建一个凭证。
        # (Act) 调用 POST /api/v1/workspaces/{ws_uuid}/credentials/service-modules/{cred_uuid}/test
        # (Assert) 如果凭证有效，返回 200 OK 和 {"status": "success", "message": "Connection successful."}
        # (Assert) 如果凭证无效，返回 400 Bad Request 和 {"status": "failed", "message": "Authentication failed with provider..."}
        pass