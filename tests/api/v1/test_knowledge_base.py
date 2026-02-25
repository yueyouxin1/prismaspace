# tests/api/v1/test_knowledge.py

import pytest
import uuid
from typing import Callable, Dict, Any, List
from decimal import Decimal
from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import ANY, AsyncMock

# --- 从 conftest 导入通用 Fixtures ---
from tests.conftest import UserContext, registered_user_with_pro, registered_user_with_free

# --- 从测试基类导入 ---
from .base.test_execution_base import BaseTestExecution

# --- 项目模型 & DAO ---
from app.models.resource import Resource, ResourceInstance, VersionStatus
from app.models.resource.knowledge import KnowledgeDocument, DocumentProcessingStatus, KnowledgeBaseVersionDocuments, KnowledgeChunk
from app.dao.resource.knowledge.knowledge_dao import KnowledgeDocumentDao
from app.engine.vector.base import SearchResult
from app.engine.model.embedding import BatchEmbeddingResult, EmbeddingResult
from app.schemas.resource.knowledge.knowledge_schemas import KnowledgeBaseInstanceConfig # 导入用于显式验证的 Schema
from app.system.vectordb.manager import resolve_collection_name_for_version

# 将此文件中所有测试标记为异步
pytestmark = pytest.mark.asyncio

# ==============================================================================
# 1. KnowledgeBase 文档管理测试套件
# ==============================================================================

@pytest.mark.usefixtures("registered_user_with_pro")
class TestKnowledgeBaseDocumentManagement:
    """测试 KnowledgeBase 实例中的文档管理（添加、列表、删除、更新）。"""

    @pytest.fixture
    async def created_knowledge_resource(self, created_resource_factory: Callable) -> Resource:
        """Fixture: 为测试创建一个基础的 KnowledgeBase 资源。"""
        return await created_resource_factory("knowledge")

    @pytest.fixture
    def workspace_instance(self, created_knowledge_resource: Resource) -> ResourceInstance:
        """提供已创建的 KnowledgeBase 资源的工作区实例。"""
        return created_knowledge_resource.workspace_instance

    @pytest.fixture
    def sample_doc_uri(self) -> str:
        """提供一个用于测试的示例文档 URI。"""
        return "https://ai-util.oss-cn-hangzhou.aliyuncs.com/audio/7dc232cb429538c65e37c35d6dba9b17_1_1730556762.pdf"

    async def test_add_document_success(
        self,
        client,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        arq_pool_mock: AsyncMock,
        workspace_instance: ResourceInstance,
        sample_doc_uri: str,
    ):
        """
        验证：添加文档成功，返回 202 Accepted 状态，并正确地将后台处理任务加入队列。
        """
        # Arrange
        headers = await auth_headers_factory(registered_user_with_pro)
        payload = {"source_uri": sample_doc_uri, "file_name": "My First Doc.pdf"}

        # Act
        response = await client.post(
            f"/api/v1/knowledge/{workspace_instance.uuid}/documents",
            json=payload,
            headers=headers,
        )

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED
        data = response.json()["data"]
        assert data["file_name"] == "My First Doc.pdf"
        assert data["status"] == DocumentProcessingStatus.PENDING.value
        assert "uuid" in data

        # 断言：后台作业已被正确地加入队列
        arq_pool_mock.enqueue_job.assert_called_once_with(
            'process_document_task',
            workspace_instance.id,
            ANY,  # 新文档的 ID 是在运行时生成的
            ANY,
            ANY,
            registered_user_with_pro.user.uuid
        )

    async def test_list_documents_in_version(
        self,
        client,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        workspace_instance: ResourceInstance,
        sample_doc_uri: str,
        arq_pool_mock: AsyncMock # 需要此 fixture 来“吞掉”添加文档时产生的 enqueue 调用
    ):
        """验证：列出文档返回了正确的数据。"""
        # Arrange: 首先添加一个文档
        headers = await auth_headers_factory(registered_user_with_pro)
        add_payload = {"source_uri": sample_doc_uri}
        add_response = await client.post(
            f"/api/v1/knowledge/{workspace_instance.uuid}/documents",
            json=add_payload,
            headers=headers,
        )
        doc_uuid = add_response.json()["data"]["uuid"]

        # Act
        response = await client.get(
            f"/api/v1/knowledge/{workspace_instance.uuid}/documents",
            headers=headers,
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()["data"]
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["uuid"] == doc_uuid
        assert "7dc232cb429538c65e37c35d6dba9b17_1_1730556762.pdf" in data["items"][0]["file_name"]

    async def test_remove_document_from_version_triggers_gc(
        self,
        client,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        db_session: AsyncSession,
        arq_pool_mock: AsyncMock,
        workspace_instance: ResourceInstance,
        sample_doc_uri: str,
    ):
        """
        验证：从一个版本中移除文档会成功，并为成为孤儿的文档触发一个垃圾回收任务。
        """
        # Arrange: 添加一个文档
        headers = await auth_headers_factory(registered_user_with_pro)
        add_payload = {"source_uri": sample_doc_uri}
        add_response = await client.post(
            f"/api/v1/knowledge/{workspace_instance.uuid}/documents", json=add_payload, headers=headers
        )
        doc_uuid = add_response.json()["data"]["uuid"]
        doc = await KnowledgeDocumentDao(db_session).get_by_uuid(doc_uuid)
        arq_pool_mock.reset_mock() # 在 'add' 调用后重置 mock，以进行干净的 'delete' 断言

        # Act
        response = await client.delete(
            f"/api/v1/knowledge/{workspace_instance.uuid}/documents/{doc_uuid}",
            headers=headers,
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "removed from this version" in response.json()["msg"]
        
        # 断言：垃圾回收作业已被正确地加入队列
        arq_pool_mock.enqueue_job.assert_called_once_with(
            'garbage_collect_document_task',
            doc.id,
            str(workspace_instance.embedding_module_version_id),
            workspace_instance.engine_alias,
            registered_user_with_pro.user.uuid
        )

    async def test_changes_to_workspace_do_not_affect_published_version(
        self,
        client,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        workspace_instance: ResourceInstance,
        publish_instance_factory: Callable,
        sample_doc_uri: str,
        arq_pool_mock: AsyncMock,
    ):
        """
        [关键业务逻辑] 验证 "写时复制" 行为：对工作区版本的更改不会影响先前发布的版本。
        """
        # Arrange
        headers = await auth_headers_factory(registered_user_with_pro)
        # 1. 添加一个初始文档并发布版本 1.0.0
        await client.post(f"/api/v1/knowledge/{workspace_instance.uuid}/documents", json={"source_uri": sample_doc_uri}, headers=headers)
        published_instance_v1 = await publish_instance_factory(workspace_instance.uuid, "1.0.0")
        
        # Act
        # 2. 向工作区版本添加一个新文档
        await client.post(f"/api/v1/knowledge/{workspace_instance.uuid}/documents", json={"source_uri": "https://ai-util.oss-cn-hangzhou.aliyuncs.com/audio/7dc232cb429538c65e37c35d6dba9b17_1_1730556762.pdf"}, headers=headers)
        
        # Assert
        # 3. 检查两个版本的文档数量
        ws_docs_resp = await client.get(f"/api/v1/knowledge/{workspace_instance.uuid}/documents", headers=headers)
        pub_docs_resp = await client.get(f"/api/v1/knowledge/{published_instance_v1.uuid}/documents", headers=headers)
        
        assert ws_docs_resp.status_code == 200
        assert pub_docs_resp.status_code == 200
        
        # 工作区版本应有 2 个文档
        assert ws_docs_resp.json()["data"]["total"] == 2
        # 已发布的版本必须仍然只有 1 个文档
        assert pub_docs_resp.json()["data"]["total"] == 1

# ==============================================================================
# 2. KnowledgeBase 执行测试套件
# ==============================================================================

@pytest.mark.usefixtures("mock_search_dependencies")
class TestKnowledgeBaseExecution(BaseTestExecution):
    """
    测试 KnowledgeBase 资源的执行 (`/execute`) 端点。
    继承了所有通用的执行场景测试（权限、版本状态等）。
    """

    resource_type: str = "knowledge"

    @pytest.fixture
    async def workspace_instance(self, workspace_instance_factory: Callable, db_session: AsyncSession) -> ResourceInstance:
        """
        KnowledgeBase 不需要特殊的配置，但我们需要在数据库中手动创建一个文档和关联关系，
        以便 `_get_version_document_manifest` 能够找到它。
        """
        instance = await workspace_instance_factory({})
        
        # 手动创建一个文档并将其与工作区版本关联
        doc = KnowledgeDocument(file_name="test.txt", source_uri="http://test.com/test.txt")
        db_session.add(doc)
        await db_session.flush() # 使用 flush 获取 doc.id

        assoc = KnowledgeBaseVersionDocuments(version_id=instance.version_id, document_id=doc.id)
        db_session.add(assoc)
        
        # [!!! CORE FIX !!!] Replace commit with flush to respect transactional testing
        await db_session.flush() 
        
        await db_session.refresh(instance)
        
        return instance

    @pytest.fixture
    def success_payload(self) -> Dict[str, Any]:
        """提供一个用于成功搜索查询的有效载荷。"""
        return {"query": "What is PrismaSpace?", "config": {"max_recall_num": 2}}

    def assert_success_output(self, response_data: Dict[str, Any]):
        """断言成功搜索结果的结构。"""
        assert "data" in response_data
        search_result = response_data["data"]
        assert "instance_uuid" in search_result
        assert "chunks" in search_result
        chunks = search_result["chunks"]
        assert isinstance(chunks, list)
        
        # 基于我们的 mock，我们期望得到 2 个结果
        assert len(chunks) == 2
        first_result = chunks[0]
        assert "uuid" in first_result
        assert "content" in first_result
        assert "score" in first_result
        assert first_result["content"] == "PrismaSpace is an amazing AI platform."

    @pytest.fixture
    def mock_search_dependencies(self, monkeypatch, vector_manager_mock):
        # 1. Mock 底层 Embedding 引擎调用（不要绕过 EmbeddingService 的计费逻辑）
        mock_embedding_result = BatchEmbeddingResult(
            total_tokens=10,
            results=[EmbeddingResult(index=0, vector=[0.1] * 128)],
        )
        mock_run_batch = AsyncMock(return_value=mock_embedding_result)
        monkeypatch.setattr("app.services.module.embedding_service.EmbeddingEngineService.run_batch", mock_run_batch)

        # --- 关键修正 ---
        # 定义我们在 Mock 中使用的假 UUID
        MOCK_DOC_UUID_1 = "doc_uuid_1"
        MOCK_DOC_UUID_2 = "doc_uuid_2"

        # 2. Mock Manifest 逻辑
        # 欺骗 Service: "嘿，不管你查哪个实例，它里面都包含 doc_uuid_1"
        # 这样 doc_to_instances_map 就会建立 'doc_uuid_1' -> instance_uuid 的映射
        async def mock_get_manifest(*args, **kwargs):
            return [MOCK_DOC_UUID_1, MOCK_DOC_UUID_2]
        
        monkeypatch.setattr("app.services.resource.knowledge.knowledge_service.KnowledgeBaseService._get_version_document_manifest", mock_get_manifest)

        # 3. Mock Vector Engine (Payload 匹配 Manifest)
        mock_vector_engine = vector_manager_mock.get_engine.return_value
        mock_search_results = [
            SearchResult(id="vec_id_1", score=0.98, payload={"document_uuid": MOCK_DOC_UUID_1}),
            SearchResult(id="vec_id_2", score=0.95, payload={"document_uuid": MOCK_DOC_UUID_2}),
        ]
        # 这里必须是 AsyncMock 的调用结果返回列表
        mock_vector_engine.search = AsyncMock(return_value=mock_search_results)

        # 4. Mock Chunk DAO (Vector ID 匹配 Engine)
        # 注意：这里我们还需要 mock payload，因为 hydration 逻辑里也可能会校验
        mock_db_chunks = [
            KnowledgeChunk(
                uuid=str(uuid.uuid4()), 
                vector_id="vec_id_1", 
                content="PrismaSpace is an amazing AI platform.", 
                document_id=1, 
                token_count=5,
                payload={"document_uuid": MOCK_DOC_UUID_1} # <--- 加上这个更保险
            ),
            KnowledgeChunk(
                uuid=str(uuid.uuid4()), 
                vector_id="vec_id_2", 
                content="It simplifies building complex applications.", 
                document_id=2, 
                token_count=6,
                payload={"document_uuid": MOCK_DOC_UUID_2}
            ),
        ]
        monkeypatch.setattr("app.services.resource.knowledge.knowledge_service.KnowledgeChunkDao.get_list", AsyncMock(return_value=mock_db_chunks))

        return mock_vector_engine # 返回以便测试中可以进一步 assert

    async def test_execute_insufficient_funds(
        self,
        monkeypatch,
        client,
        db_session: AsyncSession,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        published_instance: ResourceInstance,
        success_payload: Dict[str, Any],
    ):
        """[计费场景] 验证当账户余额不足时，执行会失败。"""
        # 避免被权益包覆盖，强制本用例仅走钱包余额路径
        monkeypatch.setattr(
            "app.services.billing.interceptor.BillingInterceptor._get_priority_entitlement_ids_for_feature",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            "app.services.billing.interceptor.BillingInterceptor._get_all_active_entitlements",
            AsyncMock(return_value=[]),
        )

        # Arrange: 设置一个极低的余额
        registered_user_with_pro.user.billing_account.balance = Decimal('0.00000001')
        await db_session.flush()
        await db_session.refresh(registered_user_with_pro.user, ['billing_account'])
        
        headers = await auth_headers_factory(registered_user_with_pro)

        # Act
        response = await client.post(
            f"/api/v1/execute/instances/{published_instance.uuid}", 
            json={"inputs": success_payload}, 
            headers=headers
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "insufficient funds" in response.json()["msg"].lower()
