# scripts/test_vector_engine.py (V3 - Final Acceptance Version)

import asyncio
import uuid
import logging
import sys
import os

# 配置日志记录
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 添加项目根目录到 sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.engine.vector.main import VectorEngineManager
from app.engine.vector.base import VectorEngineConfig, VectorEngineService, VectorChunk, VectorEngineError

# --- 测试配置 ---
TEST_MILVUS_HOST = os.getenv("TEST_MILVUS_HOST", "localhost")
TEST_MILVUS_PORT = int(os.getenv("TEST_MILVUS_PORT", 19530))
VECTOR_DIM = 8
COLLECTION_PREFIX = "test_final_"

# --- 辅助函数 ---
def print_test_header(name):
    print(f"\n{'='*20} ▶️  Running: {name} {'='*20}")

def print_pass(name):
    print(f"✅  PASSED: {name}")

def print_fail(name, error):
    print(f"❌ FAILED: {name}\n   └─ Error: {error}")
    import traceback
    traceback.print_exc()

# --- 测试运行器 ---
class TestRunner:
    def __init__(self):
        self.manager: VectorEngineManager = None
        self.engine: VectorEngineService = None
        self.collections_to_cleanup: set[str] = set()

    async def setup(self):
        print("--- Setting up test environment ---")
        config = VectorEngineConfig(engine_type="milvus", host=TEST_MILVUS_HOST, port=TEST_MILVUS_PORT, alias="test_milvus")
        self.manager = VectorEngineManager(configs=[config])
        await self.manager.startup()
        try:
            self.engine = await self.manager.get_engine("test_milvus")
            print(f"Successfully connected to Milvus at {TEST_MILVUS_HOST}:{TEST_MILVUS_PORT}.")
        except Exception as e:
            print(f"\n{'!'*60}\n  FATAL: Could not connect to Milvus.\n  Please ensure it is running at {TEST_MILVUS_HOST}:{TEST_MILVUS_PORT}.\n  Error: {e}\n{'!'*60}\n")
            raise

    async def teardown(self):
        print("\n--- Tearing down test environment ---")
        for name in list(self.collections_to_cleanup):
            try:
                await self.engine.delete_collection(name)
                logging.info(f"Cleaned up collection: {name}")
            except Exception as e:
                logging.warning(f"Failed to clean up collection {name}: {e}")
        if self.manager:
            await self.manager.shutdown()
        print("Teardown complete.")

    def get_collection_name(self) -> str:
        name = f"{COLLECTION_PREFIX}{uuid.uuid4().hex[:8]}"
        self.collections_to_cleanup.add(name)
        return name

    async def run(self):
        test_suite = [
            self.test_ddl_lifecycle,
            self.test_insert_behavior,
            self.test_upsert_behavior,
            self.test_delete_by_pks,
            self.test_delete_by_filter,
            self.test_query_by_pks,
            self.test_query_by_filter,
            self.test_search_with_filter,
            self.test_error_handling
        ]
        failed_tests = []
        for test in test_suite:
            try:
                print_test_header(test.__name__)
                await test()
                print_pass(test.__name__)
            except Exception as e:
                failed_tests.append((test.__name__, e))
                print_fail(test.__name__, e)
        return failed_tests

    # --- 测试用例 ---

    async def test_ddl_lifecycle(self):
        """1. 测试 DDL: 创建 -> 存在 -> 删除 -> 不存在"""
        name = self.get_collection_name()
        await self.engine.create_collection(name, VECTOR_DIM)
        assert self.engine.client.has_collection(name)
        await self.engine.delete_collection(name)
        assert not self.engine.client.has_collection(name)

    async def test_insert_behavior(self):
        """[最终版] 测试 Insert: 成功插入新数据，并静默忽略重复主键"""
        name = self.get_collection_name()
        await self.engine.create_collection(name, VECTOR_DIM)
        
        fixed_id = "insert-test-id-456"
        chunk1 = VectorChunk(fixed_id, [0.3] * VECTOR_DIM, {"content": "initial insert"})
        
        # 1. 首次插入
        insert_count1 = await self.engine.insert(name, [chunk1])
        assert insert_count1 == 1, "Should report 1 chunk inserted on first try."
        await asyncio.sleep(1)

        # 验证
        results1 = await self.engine.query(name, pks=[fixed_id])
        assert len(results1) == 1
        assert results1[0].payload["content"] == "initial insert"
        logging.info("Initial insert successful and verified.")

        # 2. 尝试插入重复主键
        chunk_duplicate = VectorChunk(fixed_id, [0.4] * VECTOR_DIM, {"content": "should be ignored"})
        insert_count2 = await self.engine.insert(name, [chunk_duplicate])
        # 注意：即使是静默忽略，Milvus 仍然可能报告 insert_count 为 1。我们主要关心数据的最终状态。
        logging.info(f"Second insert reported insert_count: {insert_count2}")
        await asyncio.sleep(1)

        # 3. 最终验证：数据没有被改变，也没有增加
        results2 = await self.engine.query(name, pks=[fixed_id])
        assert len(results2) == 1, "Duplicate insert should not create a new entity."
        assert results2[0].payload["content"] == "initial insert", "Duplicate insert should not overwrite existing data."
        logging.info("Verified that duplicate insert was silently ignored and data remains unchanged.")

    async def test_upsert_behavior(self):
        """2. 测试 Upsert: 插入新数据并更新已存在的数据"""
        name = self.get_collection_name()
        await self.engine.create_collection(name, VECTOR_DIM)
        
        # 插入
        chunk1 = VectorChunk("id1", [0.1] * VECTOR_DIM, {"document_uuid": "doc_A", "page": 1})
        chunk2 = VectorChunk("id2", [0.2] * VECTOR_DIM, {"document_uuid": "doc_B", "page": 2})
        await self.engine.upsert(name, [chunk1, chunk2])
        await asyncio.sleep(1)
        
        # 更新
        chunk1_updated = VectorChunk("id1", [0.11] * VECTOR_DIM, {"document_uuid": "doc_A", "page": 99})
        await self.engine.upsert(name, [chunk1_updated])
        await asyncio.sleep(1)

        results = await self.engine.query(name, pks=["id1", "id2"])
        assert len(results) == 2
        
        res_map = {r.id: r for r in results}
        assert res_map["id1"].payload["page"] == 99, "Upsert should update existing payload."
        assert res_map["id2"].payload["page"] == 2, "Upsert should not affect other chunks."

    async def test_delete_by_pks(self):
        """3a. 测试 Delete: 按主键列表删除"""
        name = self.get_collection_name()
        await self.engine.create_collection(name, VECTOR_DIM)
        chunks = [VectorChunk(f"id{i}", [0.1*i]*VECTOR_DIM, {}) for i in range(3)]
        await self.engine.upsert(name, chunks)
        await asyncio.sleep(1)

        deleted_count = await self.engine.delete(name, pks=["id0", "id2"])
        assert deleted_count == 2
        await asyncio.sleep(1)

        remaining = await self.engine.query(name, filter_expr="pk like 'id%'")
        assert len(remaining) == 1
        assert remaining[0].id == "id1"

    async def test_delete_by_filter(self):
        """3b. 测试 Delete: 按 filter 表达式删除"""
        name = self.get_collection_name()
        await self.engine.create_collection(name, VECTOR_DIM)
        chunks = [
            VectorChunk("id_A1", [0.1]*VECTOR_DIM, {"document_uuid": "doc_A"}),
            VectorChunk("id_A2", [0.2]*VECTOR_DIM, {"document_uuid": "doc_A"}),
            VectorChunk("id_B1", [0.3]*VECTOR_DIM, {"document_uuid": "doc_B"}),
        ]
        await self.engine.upsert(name, chunks)
        await asyncio.sleep(1)

        # 删除所有 document_uuid 为 'doc_A' 的 chunks
        deleted_count = await self.engine.delete(name, filter_expr='payload["document_uuid"] == "doc_A"')
        assert deleted_count == 2
        await asyncio.sleep(1)

        remaining = await self.engine.query(name, filter_expr="pk like 'id%'")
        assert len(remaining) == 1
        assert remaining[0].id == "id_B1"

    async def test_query_by_pks(self):
        """4a. 测试 Query: 按主键列表查询，并指定输出字段"""
        name = self.get_collection_name()
        await self.engine.create_collection(name, VECTOR_DIM)
        chunks = [VectorChunk("id1", [0.1]*VECTOR_DIM, {"content": "text1"}), VectorChunk("id2", [0.2]*VECTOR_DIM, {"content": "text2"})]
        await self.engine.upsert(name, chunks)
        await asyncio.sleep(1)

        # 只查询 pk 和 payload
        results = await self.engine.query(name, pks=["id2"], output_fields=["pk", "payload"])
        assert len(results) == 1
        assert results[0].id == "id2"
        assert results[0].payload["content"] == "text2"
        assert results[0].vector is None, "Vector should not be returned when not in output_fields."

    async def test_query_by_filter(self):
        """4b. 测试 Query: 按 filter 表达式查询"""
        name = self.get_collection_name()
        await self.engine.create_collection(name, VECTOR_DIM)
        chunks = [
            VectorChunk("id1", [0.1]*VECTOR_DIM, {"page": 5, "author": "Alice"}),
            VectorChunk("id2", [0.2]*VECTOR_DIM, {"page": 10, "author": "Bob"}),
            VectorChunk("id3", [0.3]*VECTOR_DIM, {"page": 15, "author": "Alice"}),
        ]
        await self.engine.upsert(name, chunks)
        await asyncio.sleep(1)

        results = await self.engine.query(name, filter_expr='payload["author"] == "Alice" and payload["page"] > 10')
        assert len(results) == 1
        assert results[0].id == "id3"

    async def test_search_with_filter(self):
        """5. 测试 Search: 向量搜索同时应用元数据过滤"""
        name = self.get_collection_name()
        await self.engine.create_collection(name, VECTOR_DIM)
        chunks = [
            VectorChunk("id_A_near", [0.9]*VECTOR_DIM, {"document_uuid": "doc_A"}), # 最相似，但会被过滤掉
            VectorChunk("id_B_far", [0.1]*VECTOR_DIM, {"document_uuid": "doc_B"}),   # 不相似
            VectorChunk("id_B_mid", [0.7]*VECTOR_DIM, {"document_uuid": "doc_B"}),  # 次相似，应该被找到
        ]
        await self.engine.upsert(name, chunks)
        await asyncio.sleep(1)

        query_vector = [0.95] * VECTOR_DIM
        # 搜索与查询向量相似，但只在 document_uuid == "doc_B" 的文档中
        results = await self.engine.search(name, query_vector, top_k=2, filter_expr='payload["document_uuid"] == "doc_B"')

        assert len(results) > 0, "Should find at least one result."
        assert results[0].id == "id_B_mid", "Search should respect filter and find the most similar chunk within the filtered set."

    async def test_error_handling(self):
        """6. 测试错误处理: 对无效输入抛出异常"""
        name = self.get_collection_name() # Get a name, but don't create it
        
        # --- [FIX] 使用 try/except/else 模式来测试异步异常 ---

        # 场景1: 对不存在的集合进行 search
        try:
            await self.engine.search(name, [0.1]*VECTOR_DIM, 1)
            # 如果没有抛出异常就执行到这里，说明测试失败
            raise AssertionError("VectorEngineError was not raised for search on non-existent collection.")
        except VectorEngineError:
            logging.info("Caught expected VectorEngineError for search.")
            pass # 测试通过

        # 场景2: 对不存在的集合进行 delete
        try:
            await self.engine.delete(name, pks=["id1"])
            raise AssertionError("VectorEngineError was not raised for delete on non-existent collection.")
        except VectorEngineError:
            logging.info("Caught expected VectorEngineError for delete.")
            pass

        # 场景3: 提供了互斥的参数
        try:
            # 注意：这里的 name 可以是一个已存在的集合，因为错误发生在参数校验阶段
            await self.engine.create_collection(name, VECTOR_DIM)
            await self.engine.query(name, pks=["id1"], filter_expr="pk > 0")
            raise AssertionError("ValueError was not raised for providing conflicting arguments.")
        except ValueError:
            logging.info("Caught expected ValueError for conflicting arguments.")
            pass


async def main():
    runner = TestRunner()
    failed_tests = []
    exit_code = 0
    try:
        await runner.setup()
        failed_tests = await runner.run()
    except Exception as e:
        print(f"\n--- A critical error occurred during test execution ---\nError: {e}")
        exit_code = 1
    finally:
        await runner.teardown()

    if failed_tests:
        print(f"\n{'!'*60}\n  SUMMARY: {len(failed_tests)} test(s) FAILED.\n")
        for name, _ in failed_tests: print(f"    - {name}")
        print(f"{'!'*60}\n")
        exit_code = 1
    elif exit_code == 0:
        print(f"\n{'*'*60}\n  SUMMARY: All VectorEngine tests PASSED!\n{'*'*60}\n")
    
    sys.exit(exit_code)

if __name__ == "__main__":
    asyncio.run(main())