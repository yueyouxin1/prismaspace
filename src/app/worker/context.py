# src/app/worker/context.py

from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.context import AppContext
from app.api.dependencies.authentication import get_base_auth_context
from app.services.permission.hierarchy import preload_permission_hierarchy
from app.services.permission.permission_evaluator import PermissionEvaluator
from app.dao.identity.user_dao import UserDao

async def rebuild_context_for_worker(ctx: dict, db_session: AsyncSession, user_uuid: Optional[str]) -> AppContext:
    """
    为后台任务安全地重建一个功能完备的 AppContext。
    """
    
    # 1. 实例化所有依赖的服务
    redis_service = ctx['redis_service']
    vector_manager = ctx['vector_manager']
    arq_pool = ctx['arq_pool']

    auth_context = None
    if user_uuid:
        # 获取 User 的最新状态
        user_dao = UserDao(db_session)
        user = await user_dao.get_by_uuid(user_uuid, withs=["billing_account"])
        if not user:
            raise RuntimeError(f"User with UUID {user_uuid} not found. Task cannot proceed.")

        permission_hierarchy = await preload_permission_hierarchy(db_session)
        
        # 2. 构建 AuthContext
        try:
            # 调用统一的、权威的构建器来创建 AuthContext
            auth_context = await get_base_auth_context(
                user_uuid=user_uuid,
                db=db_session,
                redis_service=redis_service,
                permission_hierarchy=permission_hierarchy
            )
        except Exception as e:
            # 如果构建AuthContext失败 (例如用户不存在), 任务无法继续
            print(f"Failed to rebuild auth context for worker task. User UUID: {user_uuid}. Error: {e}")
            raise RuntimeError(f"Could not initialize task context: {e}")
    
    # 3. 组装最终的 AppContext
    # 注意：返回的 AppContext 包含了一个活动的、由 `async with` 管理的 db_session
    return AppContext(
        db=db_session,
        auth=auth_context,
        redis_service=redis_service,
        vector_manager=vector_manager,
        arq_pool=arq_pool
    )