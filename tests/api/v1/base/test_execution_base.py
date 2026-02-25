# tests/api/v1/base/test_execution_base.py

import pytest
from httpx import AsyncClient
from typing import Callable, Dict, Any
from abc import ABC, abstractmethod
from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models.resource import Resource, ResourceInstance, VersionStatus
from app.dao.resource.resource_dao import ResourceInstanceDao
from tests.conftest import UserContext

class BaseTestExecution(ABC):
    """
    一个抽象的测试基类，为所有可执行资源定义了标准的测试契约。
    它包含了对不同版本状态（工作区、已发布、已归档）的执行测试。
    """
    
    # 子类必须提供的资源类型，例如 "tool", "vectordb"
    resource_type: str 

    # --- 核心 Fixtures ---

    @pytest.fixture
    async def created_resource(self, created_resource_factory: Callable) -> Resource:
        # 这个 fixture 现在可以正确地找到 conftest.py 中的 factory
        return await created_resource_factory(self.resource_type)

    @abstractmethod
    @pytest.fixture
    async def workspace_instance(self, workspace_instance_factory: Callable) -> ResourceInstance:
        pass
 
    @pytest.fixture
    async def published_instance(self, publish_instance_factory: Callable, workspace_instance: ResourceInstance) -> ResourceInstance:
        return await publish_instance_factory(workspace_instance.uuid, "1.0.0")

    @pytest.fixture
    async def archived_instance(
        self, archive_instance_factory: Callable, workspace_instance: ResourceInstance, published_instance: ResourceInstance, db_session: AsyncSession
    ) -> ResourceInstance:
        archived_instance = await archive_instance_factory(published_instance.uuid)
        assert archived_instance and archived_instance.status == VersionStatus.ARCHIVED
        assert archived_instance.resource.latest_published_instance_id == None
        return archived_instance
        
    # --- 抽象 Fixtures (子类必须实现) ---
    
    @abstractmethod
    @pytest.fixture
    def success_payload(self) -> Dict[str, Any]:
        """[契约] 子类必须提供一个用于成功执行测试的有效 `inputs` 载荷。"""
        pass

    @abstractmethod
    def assert_success_output(self, response_data: Dict[str, Any]):
        """[契约] 子类必须提供一个断言函数，用于验证成功执行后的 `data` 字段。"""
        pass

    # --- 通用测试用例 ---

    async def test_execute_published_instance_success(
        self, client: AsyncClient, db_session: AsyncSession, auth_headers_factory: Callable, registered_user_with_pro: UserContext,
        published_instance: ResourceInstance, success_payload: Dict[str, Any]
    ):
        #通用测试，提供较大的余额
        registered_user_with_pro.user.billing_account.balance = 100
        await db_session.flush()
        await db_session.refresh(registered_user_with_pro.user, ['billing_account'])
        """[通用场景] 成功执行一个已发布的版本。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        response = await client.post(
            f"/api/v1/execute/instances/{published_instance.uuid}", 
            json={"inputs": success_payload}, 
            headers=headers
        )
        print(f"DEFBUG8:{response.json()}")
        assert response.status_code == status.HTTP_200_OK
        self.assert_success_output(response.json()["data"])

    async def test_execute_workspace_instance_for_debugging(
        self, client: AsyncClient, db_session: AsyncSession, auth_headers_factory: Callable, registered_user_with_pro: UserContext,
        workspace_instance: ResourceInstance, success_payload: Dict[str, Any]
    ):
        """[通用场景] 成功执行工作区版本（用于调试）。"""
        registered_user_with_pro.user.billing_account.balance = 100
        await db_session.flush()
        await db_session.refresh(registered_user_with_pro.user, ['billing_account'])
        headers = await auth_headers_factory(registered_user_with_pro)
        response = await client.post(
            f"/api/v1/execute/instances/{workspace_instance.uuid}", 
            json={"inputs": success_payload}, 
            headers=headers
        )
        assert response.status_code == status.HTTP_200_OK
        self.assert_success_output(response.json()["data"])

    async def test_execute_archived_instance_fails(
        self, client: AsyncClient, db_session: AsyncSession, auth_headers_factory: Callable, registered_user_with_pro: UserContext,
        archived_instance: ResourceInstance, success_payload: Dict[str, Any]
    ):
        """[通用场景] 尝试执行一个已归档的版本应该失败。"""
        registered_user_with_pro.user.billing_account.balance = 100
        await db_session.flush()
        await db_session.refresh(registered_user_with_pro.user, ['billing_account'])
        headers = await auth_headers_factory(registered_user_with_pro)
        response = await client.post(
            f"/api/v1/execute/instances/{archived_instance.uuid}", 
            json={"inputs": success_payload}, 
            headers=headers
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Only published or workspace instances can be executed" in response.json()["msg"]

    @pytest.mark.parametrize(
        "instance_fixture_name", ["workspace_instance", "published_instance"]
    )
    async def test_execute_permission_denied(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_free: UserContext,
        success_payload: Dict[str, Any],
        # 1. 直接注入所有可能用到的 fixture
        workspace_instance: ResourceInstance,
        published_instance: ResourceInstance,
        # 2. 接收 parametrize 提供的字符串
        instance_fixture_name: str,
    ):
        """[通用场景] 非公开资源其他用户无法执行任何版本的实例。"""
        # 3. 根据字符串参数选择正确的、已经注入的 fixture 对象
        if instance_fixture_name == "workspace_instance":
            instance_to_test = workspace_instance
        elif instance_fixture_name == "published_instance":
            instance_to_test = published_instance
        else:
            pytest.fail(f"Unknown fixture name provided by parametrize: {instance_fixture_name}")

        headers = await auth_headers_factory(registered_user_with_free)
        response = await client.post(
            f"/api/v1/execute/instances/{instance_to_test.uuid}",
            json={"inputs": success_payload},
            headers=headers,
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    # [待办清单] 可以在基类中添加更多通用场景的测试
    @pytest.mark.skip(reason="Needs specific invalid payload from subclass.")
    async def test_execute_with_invalid_inputs_fails(self):
        """TODO: 子类可以覆盖此测试，提供一个无效的 payload 并断言 400 或 422 错误。"""
        pass