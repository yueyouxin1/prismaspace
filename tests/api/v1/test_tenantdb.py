# tests/api/v1/test_tenantdb.py

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, AsyncConnection
from sqlalchemy import text, inspect
from sqlalchemy.sql import quoted_name
from fastapi import status
from typing import Callable

# --- 从 conftest 导入通用 Fixtures ---
from tests.conftest import (
    registered_user_with_pro, 
    registered_user_with_free, 
    created_project_in_personal_ws,
    tenant_data_db_conn, # <-- 直接导入 conftest 提供的 fixture
    UserContext
)

from app.models import User, Resource
from app.dao.resource.resource_dao import ResourceDao

# 将所有测试标记为异步
pytestmark = pytest.mark.asyncio

# ==============================================================================
# 1. 核心 Fixtures (TenantDB 特有)
# ==============================================================================

@pytest.fixture
async def created_tenantdb_resource(
    client: AsyncClient, 
    auth_headers_factory: Callable,
    registered_user_with_pro: UserContext,
    created_project_in_personal_ws,
    tenant_data_db_conn: AsyncConnection,
    db_session: AsyncSession
) -> Resource:
    """
    一个完整的 fixture，负责通过API创建一个 TenantDB 资源，并在测试后清理物理 schema。
    """
    headers = await auth_headers_factory(registered_user_with_pro)
    payload = {
        "name": "My Test Database",
        "resource_type": "tenantdb",
    }
    
    response = await client.post(
        f"/api/v1/workspaces/{created_project_in_personal_ws.workspace.uuid}/resources",
        json=payload,
        headers=headers
    )
    assert response.status_code == status.HTTP_201_CREATED, f"Failed to create tenantdb resource: {response.text}"
    
    resource_uuid = response.json()["data"]["uuid"]
    resource = await ResourceDao(db_session).get_resource_details_by_uuid(resource_uuid)
    assert resource is not None
    
    yield resource
    
    # --- Cleanup ---
    if hasattr(resource, 'workspace_instance') and resource.workspace_instance:
        schema_name = resource.workspace_instance.schema_name
        print(f"\n--- [CLEANUP] Dropping schema '{schema_name}' from tenant data plane ---")
        await tenant_data_db_conn.execute(text(f"DROP SCHEMA IF EXISTS {quoted_name(schema_name, True)} CASCADE;"))
        await tenant_data_db_conn.commit()


# ==============================================================================
# 2. 测试套件：元数据管理 (DDL)
# ==============================================================================

class TestTenantDbMetadataApi:

    async def test_create_table_success_and_physical_verification(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext,
        created_tenantdb_resource: Resource, tenant_data_db_conn: AsyncConnection
    ):
        """[成功路径] 测试成功创建一个表，并进行物理验证。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        instance_uuid = created_tenantdb_resource.workspace_instance.uuid
        schema_name = created_tenantdb_resource.workspace_instance.schema_name
        
        payload = {
            "name": "customers", "label": "客户信息表",
            "columns": [
                {"name": "name", "label": "姓名", "data_type": "text", "is_nullable": False},
                {"name": "age", "label": "年龄", "data_type": "integer", "is_indexed": True},
                {"name": "email", "label": "邮箱", "data_type": "text", "is_unique": True}
            ]
        }
        
        response = await client.post(f"/api/v1/tenantdb/{instance_uuid}/tables", json=payload, headers=headers)
        assert response.status_code == status.HTTP_200_OK

        # 物理验证
        def inspect_sync(conn):
            inspector = inspect(conn)
            return (
                inspector.get_table_names(schema=schema_name),
                inspector.get_columns("customers", schema=schema_name),
                inspector.get_indexes("customers", schema=schema_name)
            )
        tables, columns, indexes = await tenant_data_db_conn.run_sync(inspect_sync)
        
        assert "customers" in tables
        col_names = {c['name'] for c in columns}
        assert col_names == {"id", "created_at", "name", "age", "email"}
        assert any(idx['name'] == 'customers_age_idx' for idx in indexes)

    async def test_create_table_with_reserved_keyword_fails(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, created_tenantdb_resource: Resource
    ):
        """[健壮性] 使用保留关键字作为表名或列名应失败。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        instance_uuid = created_tenantdb_resource.workspace_instance.uuid
        
        bad_payload = {
            "name": "user", "label": "User Table", # 'user' is a reserved keyword
            "columns": [{"name": "data", "label": "Data", "data_type": "text"}]
        }
        
        response = await client.post(f"/api/v1/tenantdb/{instance_uuid}/tables", json=bad_payload, headers=headers)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "is a reserved keyword" in response.json()["msg"]

    async def test_update_table_syncs_columns_and_indexes(
        self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext,
        created_tenantdb_resource: Resource, tenant_data_db_conn: AsyncConnection
    ):
        """[健壮性] 完整测试列的增、删、改（包括重命名和索引变更）。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        instance_uuid = created_tenantdb_resource.workspace_instance.uuid
        schema_name = created_tenantdb_resource.workspace_instance.schema_name
        
        # 1. 创建初始表
        create_res = await client.post(f"/api/v1/tenantdb/{instance_uuid}/tables", json={
            "name": "products", "label": "产品表",
            "columns": [
                {"name": "sku", "label": "SKU", "data_type": "text", "is_unique": True},
                {"name": "price", "label": "价格", "data_type": "number", "is_indexed": True},
                {"name": "to_be_deleted", "label": "待删除列", "data_type": "text"}
            ]
        }, headers=headers)
        table_uuid = create_res.json()["data"]["uuid"]
        
        # 从响应中获取所有列的 UUID
        cols_map = {c['name']: c['uuid'] for c in create_res.json()['data']['columns']}
        sku_col_uuid = cols_map['sku']
        price_col_uuid = cols_map['price']

        # 2. [核心修复] 更新 payload 以包含所有最终期望的列
        update_payload = {
            "columns": [
                # 保持 sku 不变，必须提供其 UUID 和完整定义
                {"uuid": sku_col_uuid, "name": "sku", "label": "SKU", "data_type": "text", "is_unique": True, "is_indexed": False},
                # 重命名 price 并移除索引
                {"uuid": price_col_uuid, "name": "unit_price", "label": "单价", "data_type": "number", "is_indexed": False},
                # 新增 stock 并创建索引
                {"name": "stock", "label": "库存", "data_type": "integer", "is_indexed": True}
                # to_be_deleted 被省略，因此它将被删除
            ]
        }
        
        response = await client.put(f"/api/v1/tenantdb/{instance_uuid}/tables/{table_uuid}", json=update_payload, headers=headers)
        assert response.status_code == status.HTTP_200_OK

        # 3. 物理验证
        def inspect_sync(conn):
            inspector = inspect(conn)
            # 表名现在是 'products'，因为我们从 update_payload 中移除了 name 字段
            columns = inspector.get_columns("products", schema=schema_name)
            indexes = inspector.get_indexes("products", schema=schema_name)
            return columns, indexes
        columns, indexes = await tenant_data_db_conn.run_sync(inspect_sync)

        col_names = {c['name'] for c in columns}
        assert col_names == {"id", "created_at", "sku", "unit_price", "stock"}
        
        # [最终修正] 断言逻辑
        index_on_unit_price = any('unit_price' in idx['column_names'] for idx in indexes)
        assert not index_on_unit_price, "Index on 'unit_price' (renamed from 'price') should have been dropped."

        index_on_stock = any('stock' in idx['column_names'] for idx in indexes)
        assert index_on_stock, "Index on 'stock' should have been created."


# ==============================================================================
# 3. 测试套件：数据操作 (DML)
# ==============================================================================

class TestTenantDbDataApi:
    @pytest.fixture
    async def table_for_data_tests(self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, created_tenantdb_resource: Resource):
        headers = await auth_headers_factory(registered_user_with_pro)
        instance_uuid = created_tenantdb_resource.workspace_instance.uuid
        res = await client.post(f"/api/v1/tenantdb/{instance_uuid}/tables", json={
            "name": "users", "label": "用户表",
            "columns": [
                {"name": "name", "label": "姓名", "data_type": "text"},
                {"name": "email", "label": "邮箱", "data_type": "text"},
                {"name": "age", "label": "年龄", "data_type": "integer"},
                {"name": "is_active", "label": "活跃", "data_type": "boolean"}
            ]
        }, headers=headers)
        
        # 预填充数据
        await client.post(f"/api/v1/execute/instances/{instance_uuid}", json={
            "inputs": {"action": "insert", "table_name": "users", "payload": [
                {"name": "Alice", "email": "alice@corp.com", "age": 30, "is_active": True},
                {"name": "Bob", "email": "bob@corp.com", "age": 25, "is_active": True},
                {"name": "Charlie", "email": "charlie@demo.com", "age": 35, "is_active": True},
                {"name": "Diana", "email": "diana@corp.com", "age": 45, "is_active": False},
            ]}
        }, headers=headers)
        
        return {"instance_uuid": instance_uuid, "table_name": "users"}

    async def test_query_with_advanced_filters_and_options(self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, table_for_data_tests: dict):
        """[健壮性] 测试复杂的查询、排序、分页和字段选择。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        instance_uuid = table_for_data_tests["instance_uuid"]
        
        query_payload = {
            "inputs": {
                "action": "query", "table_name": "users",
                "filters": [
                    ["age", ">", 20],
                    ["is_active", "=", True],
                    ["email", "like", "%corp.com"]
                ],
                "columns": ["name", "age"], # 只选择 name 和 age
                "order_by": "age DESC, name ASC", # 多字段排序
                "limit": 1,
                "page": 2
            }
        }
        
        response = await client.post(f"/api/v1/execute/instances/{instance_uuid}", json=query_payload, headers=headers)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]

        assert data["count"] == 2 # Alice and Bob match filters
        assert len(data["data"]) == 1 # limit is 1, page 2
        
        # 结果应为 Bob (Alice age 30, Bob age 25. DESC order means Alice is on page 1, Bob on page 2)
        assert data["data"][0]["name"] == "Bob"
        assert "email" not in data["data"][0] # 验证字段选择生效
        assert "is_active" not in data["data"][0]

    async def test_update_and_delete_with_filters(self, client: AsyncClient, auth_headers_factory: Callable, registered_user_with_pro: UserContext, table_for_data_tests: dict):
        """测试使用过滤器进行批量更新和删除。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        instance_uuid = table_for_data_tests["instance_uuid"]

        # 1. 批量更新所有 corp.com 的用户为不活跃
        update_payload = {"inputs": {"action": "update", "table_name": "users", "filters": [["email", "like", "%corp.com"]], "payload": {"is_active": False}}}
        update_res = await client.post(f"/api/v1/execute/instances/{instance_uuid}", json=update_payload, headers=headers)
        assert update_res.json()["data"]["data"] == 3 # Alice, Bob, Diana

        # 2. 删除所有年龄大于 30 的用户
        delete_payload = {"inputs": {"action": "delete", "table_name": "users", "filters": [["age", ">", 30]]}}
        delete_res = await client.post(f"/api/v1/execute/instances/{instance_uuid}", json=delete_payload, headers=headers)
        assert delete_res.json()["data"]["data"] == 2 # Charlie, Diana
        
        # 3. 验证最终剩下的用户
        response = await client.post(f"/api/v1/execute/instances/{instance_uuid}", json={"inputs": {"action": "query", "table_name": "users"}}, headers=headers)
        assert response.status_code == status.HTTP_200_OK
        final_data = response.json()["data"]
        assert final_data["count"] == 2
        remaining_names = {u['name'] for u in final_data["data"]}
        assert remaining_names == {"Alice", "Bob"}

class TestMissingTenantDbAPIs:
    """[待办清单] 记录在审查中发现的、为完善TenantDB业务闭环所需的接口。"""

    @pytest.mark.skip(reason="API [POST /tenantdb/{uuid}/tables/infer-schema] not yet implemented.")
    async def test_infer_table_schema_from_file(self):
        """
        TODO: 应该有一个接口，允许用户上传一个CSV或JSON文件，后端能自动推断出
        表结构（表名、列名、数据类型）并返回，极大地简化用户建表过程。
        """
        # (Act) 调用 POST /api/v1/tenantdb/{instance_uuid}/tables/infer-schema，上传一个CSV文件。
        # (Assert) 响应码为 200 OK，返回一个 TenantTableCreate 结构的JSON，其中 'columns' 已被预填充。
        pass

    @pytest.mark.skip(reason="API [POST /tenantdb/{uuid}/tables/{table_uuid}/import] not yet implemented.")
    async def test_import_data_from_file(self):
        """
        TODO: 应该有一个接口，允许用户上传CSV/JSON文件，并将数据批量导入到
        一个已存在的表中。这应该是一个后台任务。
        """
        # (Arrange) 创建一个表。
        # (Act) 调用 POST /api/v1/tenantdb/{instance_uuid}/tables/{table_uuid}/import，上传CSV文件。
        # (Assert) 响应码为 202 Accepted，返回一个任务ID。
        # (Assert) 后续可以轮询任务状态，最终验证数据已导入。
        pass

    @pytest.mark.skip(reason="API [GET /tenantdb/{uuid}/tables/{table_uuid}/export] not yet implemented.")
    async def test_export_data_to_file(self):
        """
        TODO: 应该有一个接口，允许用户将表中的数据（可带筛选条件）导出为CSV文件。
        """
        # (Act) 调用 GET /api/v1/tenantdb/{instance_uuid}/tables/{table_uuid}/export?format=csv&filter_age_gt=30
        # (Assert) 响应码为 200 OK，Content-Type 为 'text/csv'，并返回文件内容。
        pass
