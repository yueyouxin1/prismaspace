# app/api/dependencies/authentication.py

import logging
from fastapi import Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Dict, Set
from pydantic import BaseModel, ConfigDict
from jose import JWTError

from app.models.identity import User, Team # 确保导入 Team
from app.dao.identity.user_dao import UserDao
# from app.dao.identity.api_key_dao import ApiKeyDao # 为未来准备
from app.db.session import get_db
from app.core.security import decode_token
from app.services.permission.permission_evaluator import PermissionEvaluator
from app.services.redis_service import RedisService
from app.services.exceptions import UserNotFound

# --- 定义 AuthContext ---
class AuthContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    user: User
    evaluator: PermissionEvaluator
    team: Optional[Team] = None
    token: Optional[str] = None
    api_key: Optional[str] = None

async def get_base_auth_context(
    user_uuid: str, db: AsyncSession, redis_service: RedisService, permission_hierarchy: Dict[str, Set[str]]
) -> AuthContext:
    """
    [核心构建器]
    根据用户UUID，从数据库获取用户并构建一个完整的、可用的AuthContext。
    这是在API层和Worker层之间共享的、权威的构建逻辑。
    """
    user_dao = UserDao(db)
    
    # [关键] 预加载 billing_account 关系，这是性能和正确的关键
    user = await user_dao.get_by_uuid(user_uuid, withs=["billing_account"])
    
    if user is None:
        # 使用一个业务逻辑异常，而不是HTTPException，因为它可能在非HTTP环境（Worker）中被调用
        raise UserNotFound(f"User with UUID {user_uuid} not found.")
        
    if user.billing_account is None:
        raise RuntimeError(f"Data integrity issue: User {user.uuid} is missing a billing account.")

    evaluator = PermissionEvaluator(db, user, redis_service, permission_hierarchy)
    return AuthContext(user=user, evaluator=evaluator)

# --- [修正 2] 辅助函数保持纯粹，不依赖 request ---
async def get_auth_context_from_token(token: str, db: AsyncSession, redis_service: RedisService, permission_hierarchy: Dict[str, Set[str]]) -> AuthContext:
    """
    Decodes a JWT, fetches the user, and builds an AuthContext.
    This function is pure and does not depend on the request object.
    """
    try:
        payload = decode_token(token)
        user_uuid = payload.get("sub")
        if user_uuid is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
        base_auth_context = await get_base_auth_context(user_uuid, db, redis_service, permission_hierarchy)
        base_auth_context.token = token
        return base_auth_context
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except UserNotFound as e:
        # 将 UserNotFound 转换为适用于API层的HTTPException
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

# --- [主依赖项] ---
async def get_auth(
    request: Request, 
    db: AsyncSession = Depends(get_db)
) -> AuthContext:
    """
    The single entry point for authentication.
    It dispatches to specific auth methods and populates request.state.
    """
    token = getattr(request.state, "token", None)
    api_key_value = getattr(request.state, "api_key", None)

    redis_service = getattr(request.app.state, "redis_service", None)

    # [CRITICAL] Get the hierarchy from app.state and inject it.
    permission_hierarchy = getattr(request.app.state, "permission_hierarchy", {})
    # Do not accept None or an empty dict.
    if not permission_hierarchy:
        logging.critical("Permission hierarchy is not available on app.state! Halting request.")
        # This is a 500-level server configuration error, not a 403 Forbidden.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Permission system is not initialized. Please contact support."
        )
        
    auth_context: Optional[AuthContext] = None

    if token:
        auth_context = await get_auth_context_from_token(token, db, redis_service, permission_hierarchy)

    # [占位] 未来的 API Key 逻辑
    # elif api_key_value:
    #     auth_context = await get_auth_context_from_apikey(api_key_value, db)
    
    # 如果没有任何认证方式成功，则抛出异常
    if auth_context is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided."
        )
    return auth_context