import asyncio
import logging
from typing import Optional
from app.api.websocket.base import BaseWebSocketHandler, WSPacket
from app.core.context import AppContext
from app.db.session import SessionLocal
from app.services.resource.agent.agent_service import AgentService
from app.schemas.resource.agent.agent_schemas import AgentExecutionRequest, AgentExecutionInputs

logger = logging.getLogger(__name__)

class AgentSessionHandler(BaseWebSocketHandler):
    def __init__(self, websocket, auth_context):
        super().__init__(websocket, auth_context)
        # [关键] 仅维护一个当前正在进行的生成任务
        # 我们假设一个 WebSocket 窗口同一时间只处理一个 AI 回复
        self.current_task: Optional[asyncio.Task] = None

    async def action_chat(self, packet: WSPacket):
        """
        处理对话请求。
        策略：新请求到来时，自动取消正在进行的旧请求（如用户频繁回车）。
        """
        # 1. 互斥处理：取消上一轮
        await self._cancel_current_task()

        # 2. 启动新任务
        # 我们将具体的业务逻辑封装在 _run_chat_stream 中，并将其作为 Task 启动
        self.current_task = asyncio.create_task(
            self._run_chat_stream(packet)
        )
        
        # 3. 绑定回调，任务结束（无论成功失败）都要清理引用
        def cleanup(t):
            if self.current_task == t:
                self.current_task = None
        self.current_task.add_done_callback(cleanup)

    async def action_stop(self, packet: WSPacket):
        """
        处理停止请求。
        不需要参数，直接停止当前正在做的事。
        """
        if self.current_task and not self.current_task.done():
            logger.info(f"User requested stop for task {id(self.current_task)}")
            await self._cancel_current_task()
            await self.send("stopped", {"message": "Generation stopped by user"}, packet.request_id)
        else:
            # 前端可能因为延迟发了 stop，但任务已结束，忽略即可
            pass

    async def on_disconnect(self):
        """连接断开时，确保清理正在运行的任务"""
        await self._cancel_current_task()

    async def _cancel_current_task(self):
        """核心：优雅取消任务并等待其清理完成"""
        if self.current_task and not self.current_task.done():
            self.current_task.cancel()
            try:
                # 等待任务响应 CancelledError 并完成 finally 块（如计费、日志）
                await self.current_task
            except asyncio.CancelledError:
                pass # 预期内的异常
            except Exception as e:
                logger.error(f"Error during task cancellation: {e}")
            finally:
                self.current_task = None

    async def _run_chat_stream(self, packet: WSPacket):
        """
        实际的流式业务逻辑。
        在一个独立的 DB Session 上下文中运行。
        """
        request_id = packet.request_id
        
        # [Session Isolation] 每次生成使用独立的 DB Session
        async with SessionLocal() as db:
            try:
                # 构建上下文
                app_context = AppContext(
                    db=db,
                    auth=self.auth_context,
                    redis_service=self.websocket.app.state.redis_service,
                    vector_manager=self.websocket.app.state.vector_manager,
                    arq_pool=self.websocket.app.state.arq_pool
                )
                service = AgentService(app_context)

                # 参数解析
                agent_uuid = packet.data.get("agent_uuid")
                if not agent_uuid:
                    await self.reply_error(request_id, "Missing agent_uuid")
                    return

                try:
                    inputs_data = packet.data.get("inputs", packet.data)
                    inputs = AgentExecutionInputs(**inputs_data)
                    request = AgentExecutionRequest(inputs=inputs)
                except Exception as e:
                    await self.reply_error(request_id, f"Invalid Inputs: {e}")
                    return

                # 获取生成器
                result = await service.async_execute(agent_uuid, request, self.user)
                generator = result.generator
                # 执行流式生成
                # Service 层负责捕获 CancelledError 并进行抢救性计费
                async for agent_event in generator:
                    # 透传事件
                    await self.send(
                        event=agent_event.event,
                        data=agent_event.data,
                        request_id=request_id
                    )

            except asyncio.CancelledError:
                # 任务被外部取消（stop 或 disconnect）
                # 发送一个 cancelled 事件给前端，确认 UI 状态
                await self.send("cancelled", request_id=request_id)
                raise # 继续抛出，确保 Task 状态正确

            except Exception as e:
                logger.error(f"Stream Error: {e}", exc_info=True)
                await self.reply_error(request_id, str(e))