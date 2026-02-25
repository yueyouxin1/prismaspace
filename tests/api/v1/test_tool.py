# tests/api/v1/test_tool.py

import pytest
from httpx import AsyncClient
from typing import Callable, Dict, Any
from decimal import Decimal
from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession
# --- 从 conftest 导入通用 Fixtures ---
from tests.conftest import registered_user_with_pro, registered_user_with_free, UserContext

# --- 从测试基类导入 ---
from .base.test_execution_base import BaseTestExecution

from app.models.resource import Resource, ResourceInstance

# 将所有测试标记为异步
pytestmark = pytest.mark.asyncio

# ==============================================================================
# 1. Tool 执行测试套件
# ==============================================================================

class TestToolExecution(BaseTestExecution):
    """
    专门测试 'tool' 类型资源的执行逻辑。
    它继承了 BaseTestExecution 的所有通用执行场景测试。
    """
    
    # [契约实现] 告诉基类我们要测试的资源类型是 'tool'
    resource_type: str = "tool"

    # --- 核心 Fixtures (Tool 特有) ---

    @pytest.fixture
    async def workspace_instance(self, workspace_instance_factory: Callable) -> ResourceInstance:
        """
        [特化 Fixture] 在通用 `workspace_instance` 的基础上，为其配置 Tool 特有的属性（URL, schema 等）。
        这是所有 Tool 执行测试的前置条件。
        """
        tool_config = {
            "url": "https://wttr.in/{city}",
            "method": "GET",
            "inputs_schema": [
                {
                    "name": "city", "type": "string", "required": True, "open": True,
                    "label": "City Name", "description": "The name of the city to get the weather for.",
                    "role": "http.path"
                },
                {
                    "name": "format", "type": "string", "required": False, "open": False,
                    "role": "http.query", "value": {"type": "literal", "content": "j1"}
                }
            ],
            "outputs_schema": [
                {
                    "name": "current_condition", "type": "array", "required": True,
                    "items": {
                        "type": "object",
                        "properties": [
                            {"name": "temp_C", "type": "string", "required": True},
                            {"name": "humidity", "type": "string", "required": True},
                        ]
                    }
                }
            ],
            "visibility": "private"
        }
        return await workspace_instance_factory(tool_config)

    # [契约实现] 为基类提供 Tool 执行所需的成功载荷
    @pytest.fixture
    def success_payload(self) -> Dict[str, Any]:
        return {"city": "London"}
        
    # [契约实现] 为基类提供 Tool 成功执行后的断言逻辑
    def assert_success_output(self, response_data: Dict[str, Any]):
        assert "data" in response_data
        execution_result = response_data["data"]
        
        assert "current_condition" in execution_result
        assert isinstance(execution_result["current_condition"], list)
        assert len(execution_result["current_condition"]) > 0
        
        first_condition = execution_result["current_condition"][0]
        assert "temp_C" in first_condition
        assert "humidity" in first_condition
        assert "weatherDesc" not in first_condition # 验证塑形确实生效了

    # --- Tool 特有的测试用例 ---
        
    @pytest.mark.usefixtures("workspace_instance")
    async def test_execute_with_invalid_inputs_fails(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        auth_headers_factory: Callable,
        get_billable_cost: Callable,
        registered_user_with_pro: UserContext,
        published_instance: ResourceInstance
    ):
        """
        [健壮性] 使用不符合 inputs_schema 的载荷执行，应返回 400 错误。
        """
        # 1. 创建一个余额刚好等于或略大于单次成本的用户
        billable_tool_cost = await get_billable_cost(feature_name="limit:tool:custom:execution", usage="1")
        initial_balance = billable_tool_cost * Decimal('1.2') # 留一些余量
        registered_user_with_pro.user.billing_account.balance = initial_balance
        await db_session.flush()
        await db_session.refresh(registered_user_with_pro.user, ['billing_account'])
        headers = await auth_headers_factory(registered_user_with_pro)
        payload = {"inputs": {"location": "Berlin"}} # 参数名错误，应为 "city"
        
        response = await client.post(f"/api/v1/execute/instances/{published_instance.uuid}", json=payload, headers=headers)
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Input validation failed" in response.json()["msg"]

    @pytest.mark.usefixtures("workspace_instance")
    async def test_execute_workspace_instance_with_raw_response(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        auth_headers_factory: Callable,
        get_billable_cost: Callable,
        registered_user_with_pro: UserContext,
        workspace_instance: ResourceInstance,
        success_payload: Dict[str, Any]
    ):
        """
        [业务场景] 测试在执行工作区版本时，可以获取未经塑形的原始API响应，用于调试。
        """
        # Arrange
        billable_tool_cost = await get_billable_cost(feature_name="limit:tool:custom:execution", usage="1")
        initial_balance = billable_tool_cost * Decimal('1.2') # 留一些余量
        registered_user_with_pro.user.billing_account.balance = initial_balance
        await db_session.flush()
        await db_session.refresh(registered_user_with_pro.user, ['billing_account'])
        headers = await auth_headers_factory(registered_user_with_pro)
        # 假设 ToolService 允许一个特殊的查询参数来触发此行为
        # 注意：此功能在 `ToolService` 中尚未实现，这是一个前瞻性的测试。
        # 我们暂时在 payload 中添加一个 `_raw` 标志来模拟这个功能。
        payload = {
            "inputs": success_payload,
            "meta": {
                "return_raw_response": True
            }
        }
        
        # Act
        response = await client.post(
            f"/api/v1/execute/instances/{workspace_instance.uuid}", 
            json=payload,
            headers=headers
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        
        # 验证返回的是原始、未经塑形的完整数据
        raw_data = response.json()["data"]["data"] # 假设原始数据也放在 'data' 字段
        assert "current_condition" in raw_data
        assert "weather" in raw_data  # 'weather' 是原始API返回但被 outputs_schema 排除的字段
        
        first_condition = raw_data["current_condition"][0]
        assert "weatherDesc" in first_condition # 'weatherDesc' 也是被排除的字段

class TestMissingToolExecutionAPIs:
    """[待办清单] 记录在审查中发现的、为完善Tool执行业务闭环所需的接口。"""

    @pytest.mark.skip(reason="API [GET /instances/{uuid}/executions] not yet implemented.")
    async def test_get_execution_history_for_instance(self):
        """
        TODO: 应该有一个接口可以查看某个特定版本（Instance）的历史执行记录。
        这对于调试、审计和计费追溯非常重要。
        这个接口应该能分页、按时间范围和状态（成功/失败）进行筛选。
        """
        # (Arrange) 执行一个 tool instance 多次。
        # (Act) 调用 GET /api/v1/instances/{instance_uuid}/executions
        # (Assert) 响应码为 200 OK，返回一个包含执行记录的列表，
        #          每条记录应包含：执行ID(trace_id)、时间戳、状态、输入摘要、输出摘要、成本等。
        pass

    @pytest.mark.skip(reason="API [GET /executions/{trace_id}] not yet implemented.")
    async def test_get_detailed_execution_log(self):
        """
        TODO: 应该有一个接口可以通过执行ID（trace_id）获取一次完整执行的详细步骤和日志。
        这对调试复杂的、包含多个步骤的执行（如未来的 Agent 执行）至关重要。
        """
        # (Arrange) 执行一个 tool instance，并从响应头或响应体中获取 trace_id。
        # (Act) 调用 GET /api/v1/executions/{trace_id}
        # (Assert) 响应码为 200 OK，返回一个树状或列表结构的日志，详细展示了输入验证、
        #          HTTP请求构建、实际请求和响应、输出塑形等每一步的信息。
        pass

    @pytest.mark.skip(reason="API [POST /executions/{trace_id}/rerun] not yet implemented.")
    async def test_rerun_a_past_execution(self):
        """
        TODO: 应该有一个接口允许用户重新执行一次过去的操作，可以选择使用
        完全相同的输入，或提供新的输入。
        """
        # (Arrange) 执行一次 tool instance，获取 trace_id。
        # (Act) 调用 POST /api/v1/executions/{trace_id}/rerun，
        #       可选地在 body 中提供新的 `inputs` 来覆盖原始输入。
        # (Assert) 响应码为 200 OK，返回新一次执行的结果。
        # (Assert) 数据库中应有一条新的 trace 记录。
        pass