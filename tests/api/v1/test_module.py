# tests/api/v1/test_module.py

import pytest
from httpx import AsyncClient
from typing import Callable, Dict
from fastapi import status

# --- 从 conftest 导入通用 Fixtures ---
from tests.conftest import (
    registered_user_with_pro,
    registered_user_with_free,
    registered_user_with_team,
    created_team,
    team_workspace,
    UserContext
)

from app.models import Workspace, ServiceModuleVersion, User
from app.services.module.service_module_service import ServiceModuleService, ServiceModuleStatus
from app.services.exceptions import PermissionDeniedError, NotFoundError
from app.core.config import settings

# 将所有测试标记为异步
pytestmark = pytest.mark.asyncio


# ==============================================================================
# 1. 核心 Fixtures (本文件特有)
# ==============================================================================

@pytest.fixture
async def free_user_context(registered_user_factory: Callable) -> UserContext:
    """提供一个标准的、在 Free Plan 上的用户。"""
    return await registered_user_factory()

@pytest.fixture
def mock_openai_api_key(monkeypatch):
    """通过 monkeypatch 模拟平台级的默认 OpenAI API Key。"""
    original_key = settings.OPENAI_API_KEY
    test_key = "sk-platform-default-key-from-settings"
    monkeypatch.setattr(settings, 'OPENAI_API_KEY', test_key)
    yield test_key
    monkeypatch.setattr(settings, 'OPENAI_API_KEY', original_key)

@pytest.fixture
async def credential_in_team_ws(
    client: AsyncClient, 
    auth_headers_factory: Callable,
    registered_user_with_team: UserContext,
    team_workspace: Workspace,
    credential_payload_factory: Callable
) -> str:
    """在团队工作空间中创建一个自定义的 OpenAI 凭证。"""
    headers = await auth_headers_factory(registered_user_with_team)
    payload = await credential_payload_factory(provider_name="openai", label="Team Workspace Key", value="sk-team-workspace-custom-key")
    response = await client.post(f"/api/v1/workspaces/{team_workspace.uuid}/credentials/service-modules", json=payload, headers=headers)
    assert response.status_code == status.HTTP_201_CREATED
    return payload["value"]

# ==============================================================================
# 2. 测试套件
# ==============================================================================

class TestServiceModuleDiscovery:
    """测试服务模块的发现和列表功能。"""

    async def test_list_available_for_actor_with_permission(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext
    ):
        """[上下文可用性] Pro 用户有权使用 'gpt-4o'，应该能列出它。"""
        
        headers = await auth_headers_factory(registered_user_with_pro)
        workspace_uuid = registered_user_with_pro.personal_workspace.uuid
        
        # Act
        response = await client.get(f"/api/v1/service-modules/me/available?type=llm&workspace_uuid={workspace_uuid}", headers=headers)
        
        # Assert
        assert response.status_code == status.HTTP_200_OK
        modules = response.json()["data"]
        assert any(mod['name'] == 'gpt-4o' for mod in modules)

class TestSecureRuntimeContext:
    """[核心] 测试 get_runtime_context 方法的凭证解析优先级和安全性。"""

    @pytest.fixture
    async def gpt4o_version(self, db_session) -> ServiceModuleVersion:
        """获取 gpt-4o 的 ServiceModuleVersion ORM 对象。"""
        from app.dao.module.service_module_dao import ServiceModuleDao
        module = await ServiceModuleDao(db_session).get_one(
            where={"name": "gpt-4o"}, 
            withs=["versions"],
            joins=[{"name": "type", "where": {"name": "llm"}}]
        )
        return module.versions[0]

    async def test_runtime_context_uses_platform_default_key(
        self, app_context_factory: Callable, registered_user_with_pro: UserContext, 
        gpt4o_version: ServiceModuleVersion, mock_openai_api_key: str
    ):
        """[优先级2] 在个人工作空间中，当没有自定义密钥时，应回退到平台默认密钥。"""
        # Arrange: 在 registered_user_with_pro 下创建一个 app_context
        app_context = await app_context_factory(registered_user_with_pro.user)
        service = ServiceModuleService(app_context)
        
        # Act
        runtime_ctx = await service.get_runtime_context(
            version_id=gpt4o_version.id,
            actor=registered_user_with_pro.user,
            workspace=registered_user_with_pro.personal_workspace
        )
        
        # Assert
        assert runtime_ctx.module.name == "gpt-4o"
        assert runtime_ctx.version.id == gpt4o_version.id
        assert runtime_ctx.credential.api_key == mock_openai_api_key

    async def test_runtime_context_uses_workspace_custom_key(
        self, app_context_factory: Callable, registered_user_with_team: UserContext,
        gpt4o_version: ServiceModuleVersion, team_workspace: Workspace, 
        mock_openai_api_key: str, credential_in_team_ws: str
    ):
        """[优先级1] 在团队工作空间中，当存在自定义密钥时，应优先使用它。"""
        app_context = await app_context_factory(registered_user_with_team.user)
        service = ServiceModuleService(app_context)
        
        runtime_ctx = await service.get_runtime_context(
            version_id=gpt4o_version.id,
            actor=registered_user_with_team.user,
            workspace=team_workspace
        )
        
        assert runtime_ctx.version.id == gpt4o_version.id
        assert runtime_ctx.credential.api_key == credential_in_team_ws
        assert runtime_ctx.credential.api_key != mock_openai_api_key

    async def test_runtime_context_credential_isolation(
        self, app_context_factory: Callable, registered_user_with_pro: UserContext, registered_user_with_team: UserContext,
        gpt4o_version: ServiceModuleVersion, team_workspace: Workspace, 
        mock_openai_api_key: str, credential_in_team_ws: str
    ):
        """[隔离性] 验证在一个工作空间（团队）中设置的密钥不会影响另一个（个人）工作空间。"""
        app_context = await app_context_factory(registered_user_with_pro.user)
        service = ServiceModuleService(app_context)
        
        # 即使团队空间设置了密钥，在个人空间查询时，仍应获取平台默认密钥
        runtime_ctx = await service.get_runtime_context(
            version_id=gpt4o_version.id,
            actor=registered_user_with_pro.user,
            workspace=registered_user_with_pro.personal_workspace
        )
        
        assert runtime_ctx.credential.api_key == mock_openai_api_key
        assert runtime_ctx.credential.api_key != credential_in_team_ws

    async def test_runtime_context_for_non_existent_version_fails(
        self, app_context_factory: Callable, registered_user_with_pro: UserContext
    ):
        """[边界条件] 尝试为不存在的版本 ID 获取上下文应失败。"""
        app_context = await app_context_factory(registered_user_with_pro.user)
        service = ServiceModuleService(app_context)
        
        with pytest.raises(NotFoundError):
            await service.get_runtime_context(
                version_id=99999, # 一个不存在的 ID
                actor=registered_user_with_pro.user,
                workspace=registered_user_with_pro.personal_workspace
            )

class TestMissingModuleAPIs:
    """[待办清单] 记录在审查中发现的、为完善服务模块管理业务闭环所需的接口。"""

    @pytest.mark.skip(reason="API [GET /service-modules/{name}] not yet implemented.")
    async def test_get_service_module_details(self):
        """
        TODO: 应该有一个接口，可以获取单个服务模块（例如 'gpt-4o'）的详细信息，
        包括其所有可用版本的列表、描述、提供商等。
        这对前端构建模块详情页或选择器非常重要。
        """
        # (Act) 调用 GET /api/v1/service-modules/gpt-4o
        # (Assert) 响应码为 200 OK
        # (Assert) 响应体包含模块的详细信息和其所有版本的列表。
        pass

    @pytest.mark.skip(reason="Admin-level APIs for managing modules are not yet implemented.")
    async def test_admin_manage_service_modules(self):
        """
        TODO: 应该有一整套受 `platform:servicemodule:manage` 权限保护的管理员接口，
        用于管理（CRUD）平台上的 ServiceModuleType, ServiceModule, ServiceModuleVersion。
        这是平台能力迭代和运营的基础。
        """
        # Example: POST /api/v1/admin/service-modules
        # Example: PUT /api/v1/admin/service-modules/{name}/versions/{tag}
        pass

    @pytest.mark.skip(reason="API for checking module dependencies not yet implemented.")
    async def test_get_module_dependencies(self):
        """
        TODO: 应该有一个接口，可以查询一个服务模块版本依赖于哪些其他模块，
        或者被哪些模块所依赖。这对于影响分析和系统维护至关重要。
        """
        # Example: GET /api/v1/service-modules/versions/{id}/dependencies
        # Example: GET /api/v1/service-modules/versions/{id}/dependants
        pass