# src/app/api/dependencies/context.py

from fastapi import Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from app.core.context import AppContext
from app.db.session import get_db
from app.api.dependencies.authentication import get_auth, AuthContext

# --- 步骤1: 定义一个纯粹的基础上下文构建器 ---
async def get_base_context(
    request: Request, 
    db: AsyncSession = Depends(get_db)
) -> AppContext:
    """
    [纯粹构建器]
    只负责构建一个包含所有非认证的、全局共享依赖的 AppContext。
    它的 'auth' 字段总是 None。
    """
    return AppContext(
        db=db,
        auth=None, # 初始时 auth 为 None
        redis_service=getattr(request.app.state, "redis_service", None),
        vector_manager=getattr(request.app.state, "vector_manager", None),
        arq_pool=getattr(request.app.state, "arq_pool", None)
    )

# --- 步骤2: 定义公共/可选认证的上下文 ---
async def get_public_context(
    request: Request, 
    # 依赖于基础上下文
    context: AppContext = Depends(get_base_context),
) -> AppContext:
    """
    [公共/可选认证]
    获取基础上下文，并尝试为其填充认证信息。
    """
    try:
        context.auth = await get_auth(request, context.db)
    except HTTPException as e:
        if e.status_code in [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN]:
            context.auth = None
        else:
            raise e
    return context

# --- 步骤3: 定义强制认证的上下文 ---
async def require_auth_context(
    request: Request, 
    # 依赖于基础上下文
    context: AppContext = Depends(get_base_context),
) -> AppContext:
    """
    [强制认证]
    获取基础上下文，并强制要求 get_auth 成功。
    如果 get_auth 抛出任何 HTTPException (401, 403, etc.)，请求将在此被中断。
    """
    # 如果 get_auth 成功，auth 对象必然存在
    context.auth = await get_auth(request, context.db)
    return context

# 用于公共路由或认证可选路由
PublicContextDep = Depends(get_public_context)
# 用于需要强制认证的私有路由
AuthContextDep = Depends(require_auth_context)