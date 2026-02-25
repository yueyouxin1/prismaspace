import json
import logging
import asyncio
from typing import List, Dict, Any, Optional
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

# --- 1. 标准协议定义 ---

class WSPacket(BaseModel):
    """
    客户端请求包。
    request_id: 仅用于前端UI定位，后端原样返回，不用于逻辑控制。
    """
    action: str 
    data: Dict[str, Any] = {}
    request_id: Optional[str] = None 

class WSEvent(BaseModel):
    """服务端响应包"""
    event: str
    data: Any = None
    request_id: Optional[str] = None # 原样返回客户端传来的ID
    
    def to_text(self) -> str:
        return self.model_dump_json(exclude_none=True)

# --- 2. 连接管理器 ---

class WSConnectionManager:
    """
    管理 WebSocket 连接，支持按用户/会话分组广播。
    """
    def __init__(self):
        # 维护所有活跃连接
        self.active_connections: List[WebSocket] = []
        # 可选：按用户ID索引连接，用于单点推送 (user_id -> [ws1, ws2])
        self.user_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, user_uuid: Optional[str] = None):
        await websocket.accept()
        self.active_connections.append(websocket)
        if user_uuid:
            if user_uuid not in self.user_connections:
                self.user_connections[user_uuid] = []
            self.user_connections[user_uuid].append(websocket)
        logger.info(f"WS Connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket, user_uuid: Optional[str] = None):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        if user_uuid and user_uuid in self.user_connections:
            if websocket in self.user_connections[user_uuid]:
                self.user_connections[user_uuid].remove(websocket)
            if not self.user_connections[user_uuid]:
                del self.user_connections[user_uuid]
        logger.info(f"WS Disconnected. Total: {len(self.active_connections)}")

    async def send_personal_message(self, message: WSEvent, websocket: WebSocket):
        """发送结构化消息给特定连接"""
        try:
            await websocket.send_text(message.model_dump_json(exclude_none=True))
        except RuntimeError:
            # 连接可能已关闭
            pass

    async def broadcast(self, message: WSEvent):
        """广播给所有连接"""
        json_str = message.model_dump_json(exclude_none=True)
        for connection in self.active_connections:
            try:
                await connection.send_text(json_str)
            except RuntimeError:
                pass

# 全局单例
ws_manager = WSConnectionManager()