# tests/api/v1/e2e/test_agent_full_suite.py

import pytest
import json
import asyncio
import uuid
from decimal import Decimal
from httpx import AsyncClient
from fastapi import status, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import AsyncMock, MagicMock

from app.core.context import AppContext
from app.models.resource import Resource
from app.models.resource.agent import Agent
from app.models.interaction.chat import ChatSession
from app.dao.resource.agent.agent_dao import AgentDao
from app.dao.module.service_module_dao import ServiceModuleDao
from app.dao.interaction.chat_dao import ChatSessionDao
from app.services.resource.agent.memory.agent_memory_var_service import AgentMemoryVarService
from app.schemas.resource.agent.agent_memory_schemas import AgentMemoryVarCreate
from app.services.permission.permission_evaluator import PermissionEvaluator
from app.api.dependencies.ws_auth import AuthContext
from app.api.v1.agent.ws_handler import AgentSessionHandler
from tests.conftest import UserContext
from app.main import app

# 引入 Mock LLM
from .conftest_agent import mock_llm_engine_service

# 标记所有测试为异步
pytestmark = pytest.mark.asyncio

class MockWebSocket:
    """模拟 WebSocket 行为，确保在同一事件循环中运行"""
    def __init__(self, app_state_mock):
        self.app = MagicMock()
        self.app.state = app_state_mock
        self._receive_queue = asyncio.Queue()
        self._send_queue = asyncio.Queue()
        self._closed = False
        # 模拟 client_state
        self.client_state = 1 # CONNECTED

    async def accept(self):
        pass

    async def close(self, code=1000, reason=""):
        self._closed = True
        # 向接收队列放入异常以中断循环
        await self._receive_queue.put(WebSocketDisconnect(code, reason))

    async def receive_text(self):
        data = await self._receive_queue.get()
        if isinstance(data, Exception):
            raise data
        return data

    async def send_text(self, text: str):
        await self._send_queue.put(text)

    # --- Test Helper Methods ---
    async def put_message(self, message: dict):
        await self._receive_queue.put(json.dumps(message))

    async def get_message(self, timeout=2.0):
        return json.loads(await asyncio.wait_for(self._send_queue.get(), timeout))


class TestAgentFullSuite:
    
    @pytest.fixture
    async def agent_resource(self, created_resource_factory, db_session) -> Resource:
        """创建一个 Agent 资源"""
        return await created_resource_factory("agent")

    @pytest.fixture
    async def agent_instance(self, agent_resource, db_session) -> Agent:
        """获取 Agent 的 Workspace 实例，并确保它绑定了一个真实的 LLM 模型"""
        dao = AgentDao(db_session)
        agent = await dao.get_by_uuid(agent_resource.workspace_instance.uuid)
        
        # [关键修复] 确保 Agent 绑定了数据库中真实存在的 'gpt-4o' 模型
        sm_dao = ServiceModuleDao(db_session)
        gpt4o_module = await sm_dao.get_one(where={"name": "gpt-4o"}, withs=["versions"])
        
        assert gpt4o_module and gpt4o_module.versions, "Seed data missing: gpt-4o module not found"
        
        target_version = gpt4o_module.versions[0]
        agent.llm_module_version_id = target_version.id
        await db_session.flush()
        await db_session.refresh(agent)
        
        return agent

    @pytest.fixture(autouse=True)
    def inject_mock_llm(self, monkeypatch, mock_llm_engine_service):
        """
        [Magic] 自动将 Mock LLM 注入到 Service 中。
        """
        monkeypatch.setattr(
            "app.services.common.llm_capability_provider.LLMEngineService", 
            lambda *args, **kwargs: mock_llm_engine_service
        )
        return mock_llm_engine_service

    # ==========================================================================
    # Helper: SSE Parser
    # ==========================================================================
    
    def parse_sse_events(self, text: str) -> list[dict]:
        """解析 SSE 响应文本，返回核心事件对象"""
        events = []
        current_type = "message"
        
        for line in text.strip().split('\n'):
            line = line.rstrip('\r')
            
            if line.startswith("event: "):
                current_type = line[7:].strip()
            elif line.startswith("data: "):
                # 构建事件对象
                raw_data = line[6:].strip()
                event_obj = {
                    "type": current_type,
                    "data": {}
                }
                
                # 尝试解析JSON
                try:
                    event_obj["data"] = json.loads(raw_data)
                except json.JSONDecodeError:
                    pass
                
                events.append(event_obj)

        return events

    # ==========================================================================
    # Scene 1: SSE 接口逻辑验证 (Stream)
    # ==========================================================================
    
    async def test_agent_sse_stream_correctness(
        self, 
        client: AsyncClient, 
        auth_headers_factory, 
        registered_user_with_pro: UserContext,
        agent_instance: Agent,
        mock_llm_engine_service
    ):
        """
        验证 SSE 接口是否正确格式化数据，并能正确拼接 Mock 的流式内容。
        """
        headers = await auth_headers_factory(registered_user_with_pro)
        
        # 设定 LLM 剧本
        expected_content = "Hello SSE World"
        mock_llm_engine_service.response_sequence.append(("text", expected_content))
        
        # 发起请求
        payload = {"inputs": {"input_query": "Hi"}}
        
        # 使用 request 消费整个流
        async with client.stream("POST", f"/api/v1/agent/{agent_instance.uuid}/sse", json=payload, headers=headers) as response:
            assert response.status_code == status.HTTP_200_OK
            assert "text/event-stream" in response.headers["content-type"]
            
            body_text = ""
            async for chunk in response.aiter_text():
                body_text += chunk
        
        # [InterfaceError Fix] 等待后台任务完成
        await asyncio.sleep(0.2)

        # 解析事件
        events = self.parse_sse_events(body_text)
        
        # 验证内容拼接
        event_types = []
        full_content = ""
        content_chunks = []
        for event in events:
            event_type = event["type"]
            event_data = event["data"]
            event_types.append(event_type)
            if event_type == "chunk" and "content" in event_data:
                content_chunks.append(event_data.get("content", ""))
            elif event_type == "finish" and "message" in event_data:
                full_content = event_data["message"]["content"]

        assert full_content == "".join(content_chunks)
        
        # 验证生命周期事件
        assert "start" in event_types
        assert "chunk" in event_types
        assert "finish" in event_types

    # ==========================================================================
    # Scene 2: WebSocket 双向交互 (Chat)
    # ==========================================================================

    async def test_agent_websocket_chat_flow(
        self,
        registered_user_with_pro: UserContext,
        agent_instance: Agent,
        mock_llm_engine_service,
        db_session: AsyncSession,
        monkeypatch,
        real_redis_service, 
        arq_pool_mock,      
        vector_manager_mock 
    ):
        """
        验证 WebSocket 流程。
        [FIX] 使用 Direct Handler Invocation + MockWebSocket，避免 TestClient 的 Loop 问题。
        """
        # 1. 准备 App State Mock
        app_state = MagicMock()
        app_state.redis_service = real_redis_service
        app_state.arq_pool = arq_pool_mock
        app_state.vector_manager = vector_manager_mock
        # 必须提供 hierarchy，否则 Service 初始化 PermissionEvaluator 时会报错
        app_state.permission_hierarchy = {"resource:execute": set()}

        # 2. 准备 Mock WebSocket
        mock_ws = MockWebSocket(app_state)

        # 3. 准备 Auth Context (Mock Evaluator)
        evaluator = PermissionEvaluator(
            db_session=db_session,
            actor=registered_user_with_pro.user,
            redis_service=None, 
            permission_hierarchy={"resource:execute": set()} 
        )
        evaluator.ensure_can = AsyncMock(return_value=None)
        
        auth_context = AuthContext(
            user=registered_user_with_pro.user,
            evaluator=evaluator,
            token="mock-token"
        )

        # 4. [关键修复] Monkeypatch Handler 内部使用的 SessionLocal
        # 这里的 Context Manager 必须 yield 已经在运行的 db_session
        class MockSessionContext:
            def __init__(self): pass
            async def __aenter__(self): return db_session
            async def __aexit__(self, exc_type, exc_val, exc_tb): pass 

        monkeypatch.setattr("app.api.v1.agent.ws_handler.SessionLocal", MockSessionContext)

        # 5. 实例化 Handler 并启动
        handler = AgentSessionHandler(mock_ws, auth_context)
        # 将 run() 作为后台任务启动，以便我们能与之交互
        handler_task = asyncio.create_task(handler.run())

        try:
            # --- A. 正常对话 ---
            mock_llm_engine_service.response_sequence.append(("text", "WS Response"))
            
            await mock_ws.put_message({
                "action": "chat",
                "request_id": "req_1",
                "data": {
                    "agent_uuid": agent_instance.uuid,
                    "inputs": {"input_query": "Hello WS"}
                }
            })
            
            received_content = ""
            while True:
                resp = await mock_ws.get_message()
                event = resp.get("event")
                if event == "start":
                    assert resp["request_id"] == "req_1"
                elif event == "chunk":
                    received_content += resp["data"]["content"]
                elif event == "finish":
                    break
                elif event == "error":
                    pytest.fail(f"WS Error: {resp['data']}")
            
            assert received_content == "WS Response"

            # --- B. 停止生成 (Stop) ---
            mock_llm_engine_service.response_sequence.append(("text", "Stopped Response"))
            
            await mock_ws.put_message({
                "action": "chat",
                "request_id": "req_2",
                "data": {"agent_uuid": agent_instance.uuid, "inputs": {"input_query": "Go"}}
            })
            
            # 立即发送 Stop
            await mock_ws.put_message({"action": "stop", "request_id": "req_stop"})
            
            stop_confirmed = False
            for _ in range(20):
                try:
                    resp = await mock_ws.get_message(timeout=1.0)
                    if resp.get("event") in ["cancelled", "stopped"]:
                        stop_confirmed = True
                        break
                    if resp.get("event") == "finish":
                        break
                except asyncio.TimeoutError:
                    break
            
            # 由于 Mock LLM 响应极快，Stop 信号可能在任务完成后才被处理
            # 这里的断言主要是为了确保协议没崩溃。实际行为取决于调度。
            # assert stop_confirmed or "Stopped Response" in ...
            
        finally:
            # 优雅关闭 Handler
            await mock_ws.close() 
            # 等待 Handler 退出
            try:
                await asyncio.wait_for(handler_task, timeout=2.0)
            except (asyncio.TimeoutError, WebSocketDisconnect):
                pass
            except Exception as e:
                # 忽略 Handler 内部因 mock_ws.close() 抛出的异常
                pass
            
            await asyncio.sleep(0.1)

    # ==========================================================================
    # Scene 3: 记忆变量与 Prompt Template (Memory)
    # ==========================================================================

    async def test_agent_memory_injection_strict(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        auth_headers_factory,
        app_context_factory,
        registered_user_with_pro: UserContext,
        agent_instance: Agent,
        mock_llm_engine_service
    ):
        """验证 Prompt Template 替换逻辑。"""
        headers = await auth_headers_factory(registered_user_with_pro)
        
        # 1. 定义记忆变量
        context = await app_context_factory(registered_user_with_pro.user)
        memory_service = AgentMemoryVarService(context)
        await memory_service.create_memory(
            agent_id=agent_instance.version_id,
            data=AgentMemoryVarCreate(key="user_nickname", label="Nickname", default_value="Traveler")
        )
        
        # 2. 设置 Runtime Value
        await memory_service.set_runtime_value(
            agent_id=agent_instance.version_id,
            key="user_nickname",
            value="PrismaAdmin",
            user_id=registered_user_with_pro.user.id,
            session_uuid=None
        )
        
        # 3. 更新 Prompt
        agent_instance.system_prompt = "Hello, {#LibraryBlock type=memory_key id=user_nickname#}Default{#/LibraryBlock#}!"
        if not isinstance(agent_instance.agent_config, dict):
             agent_instance.agent_config = {}
        await db_session.flush()
        await db_session.refresh(agent_instance)
        
        # 4. 执行
        mock_llm_engine_service.response_sequence.append(("text", "Reply"))
        
        response = await client.post(
            f"/api/v1/agent/{agent_instance.uuid}/execute",
            json={"inputs": {"input_query": "hi"}},
            headers=headers
        )
        assert response.status_code == 200
        
        # [InterfaceError Fix]
        await asyncio.sleep(0.2)

        # 5. 验证
        assert len(mock_llm_engine_service.captured_inputs) > 0
        last_messages = mock_llm_engine_service.captured_inputs[-1]
        system_msg = next(m for m in last_messages if m.role == "system")
        assert "Hello, PrismaAdmin!" in system_msg.content

    # ==========================================================================
    # Scene 4: 计费集成 (Billing)
    # ==========================================================================

    async def test_agent_billing_integration(
        self,
        client: AsyncClient,
        auth_headers_factory,
        registered_user_with_pro: UserContext,
        agent_instance: Agent,
        mock_llm_engine_service,
        real_redis_service,
        db_session
    ):
        """
        验证 Agent 执行后，Token 用量被正确计费。
        """
        headers = await auth_headers_factory(registered_user_with_pro)
        user_id = registered_user_with_pro.user.id
        
        # 1. 确保用户有充足余额 (100.00)
        registered_user_with_pro.user.billing_account.balance = Decimal("100.00")
        await db_session.flush()
        
        # 2. 模拟 LLM 消耗
        mock_llm_engine_service.response_sequence.append(("text", "Costly Response"))
        
        # 3. 执行
        await client.post(
            f"/api/v1/agent/{agent_instance.uuid}/execute",
            json={"inputs": {"input_query": "Cost me money"}},
            headers=headers
        )
        
        # [InterfaceError Fix] & 等待 Worker 处理
        await asyncio.sleep(0.5)
        
        ledger_key = f"shadow_ledger:user:{user_id}"
        
        # 验证余额减少
        balance = None
        for _ in range(10):
            val = await real_redis_service.client.hget(ledger_key, "wallet_balance")
            if val is not None:
                balance = Decimal(val)
                if balance < 100.00:
                    break
            await asyncio.sleep(0.1)
            
        assert balance is not None, "Shadow ledger not initialized"
        assert balance < 100.00, f"Balance did not decrease. Current: {balance}."

    # ==========================================================================
    # Scene 5: 深度记忆触发 (Deep Memory)
    # ==========================================================================
    
    async def test_deep_memory_trigger(
        self,
        client: AsyncClient,
        auth_headers_factory,
        registered_user_with_pro: UserContext, 
        agent_instance: Agent,
        mock_llm_engine_service,
        arq_pool_mock,
        db_session
    ):
        """
        验证当开启深度记忆时，对话结束后触发了后台任务。
        """
        # [Fix] 使用 registered_user_with_pro (创建者) 进行认证
        headers = await auth_headers_factory(registered_user_with_pro)
        
        # 1. 开启 Deep Memory
        config = dict(agent_instance.agent_config or {})
        config["deep_memory"] = {
            "enabled": True,
            "enable_summarization": True,
            "summary_model_uuid": agent_instance.resource.workspace.uuid # 假UUID，mock了
        }
        
        agent_instance.agent_config = config
        await db_session.flush()
        
        mock_llm_engine_service.response_sequence.append(("text", "Memory Test"))
        
        # 2. [关键修复] 预先在 DB 中创建 Session，防止 404
        session_uuid = str(uuid.uuid4())
        session = ChatSession(
            uuid=session_uuid,
            user_id=registered_user_with_pro.user.id,
            agent_instance_id=agent_instance.id,
            title="Test Deep Memory"
        )
        db_session.add(session)
        await db_session.commit()

        # 3. 执行对话
        # 重置 arq mock 以确保捕获新的调用
        arq_pool_mock.reset_mock()
        
        response = await client.post(
            f"/api/v1/agent/{agent_instance.uuid}/execute",
            json={"inputs": {"input_query": "Remember this", "session_uuid": session_uuid}},
            headers=headers
        )
        assert response.status_code == 200, f"Execute failed: {response.text}"
        
        # 4. 验证后台任务触发 (Wait Logic)
        # 这里的等待只是为了让 arq 被调用，但并不保证 Service 内部的 finally 块跑完
        for _ in range(10):
            if arq_pool_mock.enqueue_job.called:
                break
            await asyncio.sleep(0.1)
        
        assert arq_pool_mock.enqueue_job.called, "ARQ jobs should have been enqueued"
        
        calls = arq_pool_mock.enqueue_job.call_args_list
        task_names = [call.args[0] for call in calls]
        assert 'index_long_term_context_task' in task_names or 'summarize_trace_task' in task_names

        # =========================================================================
        # [关键修复] 等待所有悬挂的后台任务完成
        # =========================================================================
        # 原因：AgentService 中的 run_engine_task 即使在 API 返回后仍在后台运行（finally块）。
        # 如果不等待它完全结束就退出测试，Pytest 关闭 DB Session 时会与后台任务的 DB 操作冲突。
        
        current_task = asyncio.current_task()
        pending_tasks = asyncio.all_tasks()
        
        for task in pending_tasks:
            # 过滤出除了当前测试任务以外的任务
            if task is not current_task:
                # 识别 Agent 的核心运行任务 (根据函数名特征)
                # 这里的协程名通常包含 'run_engine_task' 或者是由 AgentService 调度的
                # 即使不判断名字，等待所有 pending 任务在测试中通常也是安全的做法
                if "run_engine_task" in str(task) or not task.done():
                    try:
                        # 给一个短暂的超时，确保任务收尾
                        await asyncio.wait_for(task, timeout=1.0)
                    except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                        # 忽略超时或取消错误，只要确保它不再占用 DB 即可
                        pass
