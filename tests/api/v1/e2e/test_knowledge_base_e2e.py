# tests/api/v1/test_knowledge_e2e.py

import asyncio
import pytest
import uuid
import time
import json
from typing import Callable, Dict, Any, List, AsyncGenerator
from httpx import AsyncClient, ConnectError, TimeoutException
from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from unittest.mock import AsyncMock
import redis.asyncio as aioredis
# --- 从 conftest 导入通用 Fixtures ---
from tests.conftest import (
    UserContext, 
    registered_user_with_pro,
    real_arq_pool,
    wait_for_job_completion,
    TestSessionLocal,
    real_redis_service
)
from app.core.config import settings
from app.engine.vector.main import VectorEngineManager, VectorEngineConfig
from app.services.redis_service import RedisService
from app.system.vectordb.manager import resolve_collection_name_for_version
from app.models.resource import Resource, ResourceInstance
from app.models.resource.knowledge import KnowledgeDocument, DocumentProcessingStatus, KnowledgeChunk
from app.dao.resource.knowledge.knowledge_dao import KnowledgeDocumentDao, KnowledgeChunkDao
from app.dao.resource.resource_dao import ResourceInstanceDao
from arq.worker import Worker
from arq.connections import ArqRedis

# 将此文件中所有测试标记为异步
pytestmark = pytest.mark.asyncio

async def poll_task_status(
    client: AsyncClient, 
    headers: Dict[str, str], 
    task_id: str, 
    timeout: int = 60
) -> Dict[str, Any]:
    """
    通过SSE端点轮询任务状态，使用httpx的流式响应。
    """
    start_time = time.time()
    url = f"{client.base_url}/api/v1/knowledge/tasks/{task_id}/progress"
    
    print(f"\n--- [Polling] Starting to poll task {task_id} via SSE at {url} ---")

    while time.time() - start_time < timeout:
        try:
            async with client.stream("GET", url, headers=headers, timeout=60.0) as response:
                if response.status_code != 200:
                    print(f"--- [Polling] Request failed with status {response.status_code} ---")
                    await asyncio.sleep(2)
                    continue
                    
                async for line in response.aiter_lines():
                    if line.startswith('data:'):
                        # 解析SSE数据
                        data_str = line[5:].strip()  # 去掉 "data: " 前缀
                        if data_str:
                            try:
                                data = json.loads(data_str)
                                print(f"--- [Polling] Progress for {task_id}: {data.get('status')} - {data.get('message')} ({data.get('progress', 0)}/{data.get('total', 0)}) ---")
                                
                                if data.get("status") in ["completed", "failed"]:
                                    print(f"--- [Polling] Task {task_id} finished with status: {data.get('status')} ---")
                                    return data
                            except json.JSONDecodeError:
                                print(f"--- [Polling] Failed to parse SSE data: {data_str} ---")
                    
                    elif line.startswith('event:error'):
                        # 处理错误事件
                        error_data_str = next_line[5:].strip() if next_line.startswith('data:') else "{}"
                        error_data = json.loads(error_data_str)
                        print(f"--- [Polling] Received error for {task_id}: {error_data.get('message', 'Unknown error')} ---")
                        raise AssertionError(f"Task failed with error: {error_data.get('message', 'Unknown error')}")

        except ConnectError as e:
            # 连接错误，重试
            print(f"--- [Polling] Connection error, retrying... ({e}) ---")
            await asyncio.sleep(2)
        except Exception as e:
            print(f"--- [Polling] An unexpected error occurred during polling: {e} ---")
            raise

    raise TimeoutError(f"Task {task_id} did not complete within {timeout} seconds.")

# ==============================================================================
# 1. E2E 专属 Fixture 定义 (覆盖 conftest.py 中的 mocks)
# ==============================================================================

@pytest.fixture(scope="module")
async def e2e_vector_manager() -> AsyncGenerator[VectorEngineManager, None]:
    """
    [E2E OVERRIDE] 创建并管理一个真实的 VectorEngineManager 的生命周期。
    scope="module" 确保整个测试文件共享同一个 manager 实例，提高效率。
    """
    print("\n--- [E2E Setup] Initializing REAL VectorEngineManager for the module. ---")
    engine_configs = [VectorEngineConfig(**config_dict) for config_dict in settings.VECTOR_ENGINE_CONFIGS]
    manager = VectorEngineManager(configs=engine_configs)
    await manager.startup()
    yield manager
    print("\n--- [E2E Teardown] Shutting down REAL VectorEngineManager. ---")
    await manager.shutdown()

@pytest.fixture
def vector_manager_mock(e2e_vector_manager: VectorEngineManager) -> VectorEngineManager:
    """
    [E2E OVERRIDE] 
    这个 fixture 与 conftest.py 中的 mock 同名。
    当在当前文件中使用时，它将优先被解析，从而将一个真实的 manager
    注入到依赖它的 `client` fixture 中。
    """
    return e2e_vector_manager

# ==============================================================================
# 2. E2E 测试套件
# ==============================================================================

@pytest.mark.e2e
class TestKnowledgeBaseE2E:
    """
    端到端测试套件，用于验证 KnowledgeBase 从 API 到物理存储的完整生命周期。
    """

    @pytest.fixture
    async def managed_knowledge_resource(
        self,
        client: AsyncClient,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        created_resource_factory: Callable,
        db_session: AsyncSession,
        e2e_vector_manager: VectorEngineManager
    ) -> AsyncGenerator[ResourceInstance, None]:
        """
        [E2E 核心 Fixture]
        1. 通过 API 创建一个 KnowledgeBase 资源。
        2. `yield` 其工作区实例给测试函数。
        3. 在测试结束后，通过 `finally` 块确保物理 Milvus Collection 被删除。
        """
        resource = await created_resource_factory("knowledge")
        workspace_instance = await ResourceInstanceDao(db_session).get_by_uuid(resource.workspace_instance.uuid)
        assert workspace_instance is not None
        
        yield workspace_instance

        collection_name = resolve_collection_name_for_version(workspace_instance.embedding_module_version)
        
        print(f"\n--- [E2E Cleanup] Dropping Milvus collection: {collection_name} ---")
        """
        try:
            vector_engine = await e2e_vector_manager.get_engine(workspace_instance.engine_alias)
            await vector_engine.delete_collection(collection_name)
        except Exception as e:
            print(f"--- [E2E Cleanup] WARNING: Failed to drop collection. Manual cleanup may be needed. Error: {e} ---")
        """

    @pytest.fixture
    def sample_doc_uri(self) -> str:
        """提供一个真实可访问的 PDF 文件 URL 用于测试。"""
        return "https://ai-util.oss-cn-hangzhou.aliyuncs.com/audio/7dc232cb429538c65e37c35d6dba9b17_1_1730556762.pdf" # "Attention Is All You Need" 论文

    async def test_full_document_processing_lifecycle_and_search(
        self,
        client,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        arq_pool_mock: AsyncMock,
        real_arq_pool: ArqRedis,
        db_session: AsyncSession,
        managed_knowledge_resource: ResourceInstance,
        e2e_vector_manager: VectorEngineManager,
        arq_worker_for_test: Worker,
        sample_doc_uri: str,
    ):
        """
        一个完整的 E2E 测试，覆盖了从文档添加到最终可搜索的整个流程。
        """
        headers = await auth_headers_factory(registered_user_with_pro)
        workspace_instance = managed_knowledge_resource
        
        # ==========================================================
        # === 阶段 1: 添加文档并等待处理 ===
        # ==========================================================
        
        # --- Act 1: Add Document ---
        await db_session.commit()
        add_payload = {"source_uri": sample_doc_uri, "file_name": "AttentionPaper.pdf"}
        add_response = await client.post(
            f"/api/v1/knowledge/{workspace_instance.uuid}/documents",
            json=add_payload,
            headers=headers,
        )
        await db_session.commit()
        assert add_response.status_code == status.HTTP_202_ACCEPTED
        
        task_id = add_response.json()["data"]["uuid"]

        # 等待任务完成
        final_status = await poll_task_status(client, headers, task_id, timeout=60)
        assert final_status["status"] == "completed"

        # --- Assert 1: Physical State Verification ---
        
        # PostgreSQL Assertions (刷新对象以获取 worker 更新后的状态)
        doc = await KnowledgeDocumentDao(db_session).get_one(where={"source_uri": sample_doc_uri})
        assert doc is not None
        assert doc.status == DocumentProcessingStatus.COMPLETED
        assert doc.chunk_count > 0
        assert doc.token_count > 0
        
        chunks = await KnowledgeChunkDao(db_session).get_list(where={"document_id": doc.id})
        assert len(chunks) == doc.chunk_count
        assert all(c.vector_id is not None for c in chunks)
        print(f"--- [E2E Assert] Found {len(chunks)} chunks in PostgreSQL for the document. ---")
        
        # Milvus Assertions (使用 E2E Manager)
        await asyncio.sleep(1)
        collection_name = resolve_collection_name_for_version(workspace_instance.embedding_module_version)
        vector_engine = await e2e_vector_manager.get_engine(workspace_instance.engine_alias)
        milvus_results = await vector_engine.query(
            collection_name=collection_name,
            pks=[c.vector_id for c in chunks]
        )
        assert len(milvus_results) == len(chunks)
        print(f"--- [E2E Assert] Found {len(milvus_results)} vectors in Milvus. ---")

        arq_pool_mock.reset_mock()
        if hasattr(arq_pool_mock, 'captured_job_result'):
            del arq_pool_mock.captured_job_result

        # ==========================================================
        # === 阶段 2: 执行搜索查询 (现在将命中真实的 Milvus) ===
        # ==========================================================
        
        # --- Act 3: Execute Search ---
        await db_session.commit()
        search_payload = {"inputs": {"query": "有车可以贷款吗？", "config": {"max_recall_num": 10}}}
        search_response = await client.post(
            f"/api/v1/execute/instances/{workspace_instance.uuid}",
            json=search_payload,
            headers=headers
        )

        # --- Assert 2: Search Result Verification ---
        assert search_response.status_code == status.HTTP_200_OK

        # 等待后台任务处理完成，避免过早回滚
        arq_pool_mock.enqueue_job.assert_called_once()
        job_id_2 = arq_pool_mock.captured_job_result.job_id
        await wait_for_job_completion(real_arq_pool, job_id_2)

        search_data = search_response.json()["data"]["data"]
        search_chunks = search_data["chunks"]
        assert isinstance(search_chunks, list)
        assert len(search_chunks) > 0 # 真实搜索结果数量可能不精确
        
        first_result = search_chunks[0]
        assert "uuid" in first_result and "content" in first_result and "score" in first_result
        assert isinstance(first_result["score"], float)
        assert "车" in str(search_chunks)
        print(f"--- [E2E Assert] REAL search successful. Top result preview: '{search_chunks}' ---")

    async def test_versioning_and_chunk_update_isolation(
        self,
        client,
        auth_headers_factory: Callable,
        registered_user_with_pro: UserContext,
        db_session: AsyncSession,
        managed_knowledge_resource: ResourceInstance,
        e2e_vector_manager: VectorEngineManager,
        arq_worker_for_test: Worker,
        sample_doc_uri: str,
    ):
        """
        测试版本隔离的核心场景：
        1. 添加文档到工作区版本 v_ws。
        2. 发布 v_ws 成为 v1.0。
        3. 在 v_ws 中更新一个块。
        4. 验证：v_ws 中的搜索结果已更新，而 v1.0 中的搜索结果保持不变。
        """
        headers = await auth_headers_factory(registered_user_with_pro)
        v_ws = managed_knowledge_resource

        # === 阶段 1: 创建初始版本 v1.0 ===
        
        # 添加文档并等待处理
        await db_session.commit()
        add_resp = await client.post(f"/api/v1/knowledge/{v_ws.uuid}/documents", json={"source_uri": sample_doc_uri}, headers=headers)
        await db_session.commit()
        assert add_resp.status_code == status.HTTP_202_ACCEPTED

        task_id_add = add_resp.json()["data"]["uuid"]
        await poll_task_status(client, headers, task_id_add, timeout=60)

        # 发布新版本
        await db_session.commit()
        publish_resp = await client.post(f"/api/v1/instances/{v_ws.uuid}/publish", json={"version_tag": "1.0.0"}, headers=headers)
        assert publish_resp.status_code == status.HTTP_201_CREATED
        v1_uuid = publish_resp.json()["data"]["uuid"]
        print(f"--- [E2E Test] Published version 1.0 with UUID: {v1_uuid} ---")

        # === 阶段 2: 在工作区版本中更新一个块 ===

        # 获取工作区版本中的一个块用于更新
        doc = await KnowledgeDocumentDao(db_session).get_one(where={"uuid": task_id_add})
        chunks_v_ws = await KnowledgeChunkDao(db_session).get_list(where={"document_id": doc.id})
        chunk_to_update = chunks_v_ws[0]
        original_content = chunk_to_update.content
        
        # 定义一个独特的、可搜索的更新内容
        UNIQUE_UPDATE_PHRASE = "AlphaGo defeated Lee Sedol in a landmark Go match."
        update_payload = {"updates": {str(chunk_to_update.uuid): UNIQUE_UPDATE_PHRASE}}
        
        # 发起批量更新请求
        await db_session.commit()
        update_resp = await client.put(f"/api/v1/knowledge/{v_ws.uuid}/chunks", json=update_payload, headers=headers)
        await db_session.commit()
        assert update_resp.status_code == status.HTTP_202_ACCEPTED
        
        # 等待所有相关的后台任务完成（这次我们无法通过任务ID追踪，只能简单等待）
        print("--- [E2E Test] Waiting for chunk update and GC tasks to process... ---")
        await asyncio.sleep(10) # 这是一个不精确但可接受的等待

        # === 阶段 3: 验证版本隔离 ===

        # --- 断言 3a: 在工作区版本中搜索，应该能找到新内容 ---
        await db_session.commit()
        search_v_ws_resp = await client.post(f"/api/v1/execute/instances/{v_ws.uuid}", json={"inputs": {"query": "AlphaGo", "config": {"max_recall_num": 1}}}, headers=headers)
        assert search_v_ws_resp.status_code == status.HTTP_200_OK
        search_v_ws_data = search_v_ws_resp.json()["data"]["data"]
        v_ws_chunks = search_v_ws_data["chunks"]
        assert len(v_ws_chunks) > 0
        assert "AlphaGo" in v_ws_chunks[0]["content"]
        print("--- [E2E Assert] ✅ SUCCESS: Updated content found in workspace version. ---")

        # --- 断言 3b: 在 v1.0 版本中搜索，**不应该**找到新内容，而应该找到旧内容 ---
        search_v1_resp = await client.post(f"/api/v1/execute/instances/{v1_uuid}", json={"inputs": {"query": "AlphaGo", "config": {"max_recall_num": 1}}}, headers=headers)
        assert search_v1_resp.status_code == status.HTTP_200_OK
        search_v1_data = search_v1_resp.json()["data"]["data"]
        search_v1_chunks = search_v1_data["chunks"]
        # 结果可能为空，或者包含不相关的其他块
        if search_v1_chunks:
            assert "AlphaGo" not in search_v1_chunks[0]["content"]
        print("--- [E2E Assert] ✅ SUCCESS: Updated content NOT found in published version 1.0. ---")
        
        # --- 断言 3c (更强): 在 v1.0 中搜索旧内容，应该能找到 ---
        original_query_word = original_content.split()[0] # 用旧内容的第一个词搜索
        search_v1_original_resp = await client.post(f"/api/v1/execute/instances/{v1_uuid}", json={"inputs": {"query": original_query_word, "config": {"max_recall_num": 1}}}, headers=headers)
        assert search_v1_original_resp.status_code == status.HTTP_200_OK
        search_v1_original_data = search_v1_original_resp.json()["data"]["data"]
        search_v1_original_chunks = search_v1_original_data["chunks"]
        assert len(search_v1_original_chunks) > 0
        assert original_query_word in search_v1_original_chunks[0]["content"]
        print(f"--- [E2E Assert] ✅ SUCCESS: Original content '{original_query_word}' still searchable in version 1.0. ---")