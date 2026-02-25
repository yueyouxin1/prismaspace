from fastapi import WebSocket, Query, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.db.session import SessionLocal
from app.services.redis_service import RedisService
from app.api.dependencies.authentication import get_auth_context_from_token, AuthContext
from app.services.permission.hierarchy import preload_permission_hierarchy

async def get_ws_auth(
    websocket: WebSocket,
    token: Optional[str] = Query(None, description="JWT Token passed via query param"),
) -> AuthContext:
    """
    WebSocket 握手阶段的认证依赖。
    如果认证失败，直接关闭连接 (Close Code 1008 Policy Violation)。
    """
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing authentication token")
        raise HTTPException(status_code=403, detail="Missing authentication token")

    # 手动创建 DB Session，因为 WebSocket 的依赖注入生命周期与 HTTP 不同
    async with SessionLocal() as db:
        redis_service: RedisService = websocket.app.state.redis_service
        permission_hierarchy = websocket.app.state.permission_hierarchy
        
        try:
            auth_context = await get_auth_context_from_token(
                token, db, redis_service, permission_hierarchy
            )
            return auth_context
        except Exception as e:
            # 认证失败，拒绝握手
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid authentication credentials")
            raise HTTPException(status_code=403, detail="Invalid credentials")