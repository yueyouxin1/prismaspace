import logging
import json
import asyncio
from typing import Any
from fastapi import WebSocket, WebSocketDisconnect
from app.utils.websocket_manager import ws_manager, WSPacket, WSEvent
from app.api.dependencies.authentication import AuthContext

logger = logging.getLogger(__name__)

class BaseWebSocketHandler:
    def __init__(self, websocket: WebSocket, auth_context: AuthContext):
        self.websocket = websocket
        self.user = auth_context.user
        self.auth_context = auth_context

    async def run(self):
        """主事件循环"""
        await ws_manager.connect(self.websocket, user_uuid=self.user.uuid)
        try:
            while True:
                text = await self.websocket.receive_text()
                await self._dispatch(text)
        except WebSocketDisconnect:
            logger.info(f"WS Disconnect: {self.user.uuid}")
        except Exception as e:
            logger.error(f"WS Loop Error: {e}", exc_info=True)
        finally:
            await self.on_disconnect() # 钩子：允许子类清理任务
            ws_manager.disconnect(self.websocket, user_uuid=self.user.uuid)

    async def _dispatch(self, text: str):
        """动态路由分发"""
        try:
            payload = json.loads(text)
            packet = WSPacket(**payload)
        except Exception as e:
            await self.reply_error(None, f"Protocol Error: {e}")
            return

        # 约定：action="chat" -> 路由到 self.action_chat(packet)
        method_name = f"action_{packet.action}"
        if hasattr(self, method_name):
            handler = getattr(self, method_name)
            # 注意：这里我们不自动 create_task，交由具体的 Handler 决定
            # 某些操作（如 Ping）是极快的，不需要 Task 开销；
            # 某些操作（如 Chat）是长时的，需要由子类管理其 Task 生命周期。
            try:
                await handler(packet)
            except Exception as e:
                logger.error(f"Action '{packet.action}' failed: {e}", exc_info=True)
                await self.reply_error(packet.request_id, str(e))
        else:
            await self.reply_error(packet.request_id, f"Unknown action: {packet.action}")

    async def send(self, event: str, data: Any = None, request_id: str = None):
        """发送辅助方法"""
        packet = WSEvent(event=event, data=data, request_id=request_id)
        try:
            await self.websocket.send_text(packet.to_text())
        except RuntimeError:
            pass

    async def reply_error(self, request_id: str, message: str):
        await self.send("error", {"message": message}, request_id)

    async def on_disconnect(self):
        """子类覆盖此方法进行清理"""
        pass
    
    # --- 通用 Action ---
    async def action_ping(self, packet: WSPacket):
        await self.send("pong", "pong", packet.request_id)