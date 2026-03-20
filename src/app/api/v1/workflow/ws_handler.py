# src/app/api/v1/workflow/ws_handler.py

import asyncio
import logging
import json
from typing import Optional
from app.api.websocket.base import BaseWebSocketHandler, WSPacket
from app.core.context import AppContext
from app.db.session import SessionLocal
from app.services.resource.workflow.workflow_service import WorkflowService
from app.schemas.resource.workflow.workflow_schemas import WorkflowExecutionRequest

logger = logging.getLogger(__name__)

class WorkflowSessionHandler(BaseWebSocketHandler):
    """
    Workflow 专用 WebSocket 处理器。
    支持 run, stop 指令，并推送全量事件。
    """
    def __init__(self, websocket, auth_context):
        super().__init__(websocket, auth_context)
        self.current_task: Optional[asyncio.Task] = None
        self.current_run_id: Optional[str] = None
        self.current_detach = None
        self.current_cancel = None

    async def action_run(self, packet: WSPacket):
        """
        执行工作流。
        """
        # 1. 互斥处理：取消上一轮
        await self._cancel_current_task()

        # 2. 启动新任务
        self.current_task = asyncio.create_task(
            self._run_workflow_stream(packet)
        )
        
        def cleanup(t):
            if self.current_task == t:
                self.current_task = None
                self.current_run_id = None
                self.current_detach = None
                self.current_cancel = None
        self.current_task.add_done_callback(cleanup)

    async def action_stop(self, packet: WSPacket):
        """停止当前执行"""
        if self.current_task and not self.current_task.done():
            logger.info(f"User requested stop for workflow task {id(self.current_task)}")
            await self._cancel_current_task()
            await self.send("stopped", {"message": "Execution stopped by user"}, packet.request_id)

    async def on_disconnect(self):
        if callable(self.current_detach):
            self.current_detach()

    async def _cancel_current_task(self):
        if callable(self.current_cancel):
            self.current_cancel()
        if self.current_task and not self.current_task.done():
            self.current_task.cancel()
            try:
                await self.current_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Error during task cancellation: {e}")
            finally:
                self.current_task = None
                self.current_run_id = None
                self.current_detach = None
                self.current_cancel = None

    async def _run_workflow_stream(self, packet: WSPacket):
        request_id = packet.request_id
        
        # [Session Isolation] 每次运行使用独立的 DB Session
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
                service = WorkflowService(app_context)

                # 参数解析
                instance_uuid = packet.data.get("instance_uuid")
                if not instance_uuid:
                    await self.reply_error(request_id, "Missing instance_uuid")
                    return

                try:
                    inputs_data = packet.data.get("inputs", {})
                    # 允许 inputs 为空
                    request = WorkflowExecutionRequest(inputs=inputs_data)
                except Exception as e:
                    await self.reply_error(request_id, f"Invalid Params: {e}")
                    return

                # 获取生成器
                result = await service.async_execute(instance_uuid, request, self.user)
                generator = result.generator
                self.current_run_id = result.run_id
                self.current_detach = getattr(result, "detach", None)
                self.current_cancel = getattr(result, "cancel", None)
                
                # 执行流式生成
                try:
                    async for event in generator:
                        # 透传 WorkflowEvent
                        await self.send(
                            event=event.event,
                            data=event.data,
                            request_id=request_id
                        )
                finally:
                    if result.task and not result.task.done():
                        try:
                            await result.task
                        except Exception:
                            pass

            except asyncio.CancelledError:
                await self.send("cancelled", request_id=request_id)
                raise

            except Exception as e:
                logger.error(f"Workflow Stream Error: {e}", exc_info=True)
                await self.reply_error(request_id, str(e))
