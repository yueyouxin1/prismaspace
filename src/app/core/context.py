# src/app/core/context.py

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from arq.connections import ArqRedis

# 导入 AuthContext 和其他服务接口
from app.api.dependencies.authentication import AuthContext
from app.services.permission.permission_evaluator import PermissionEvaluator
from app.services.redis_service import RedisService
from app.engine.model.embedding import VectorCache
from app.engine.vector.main import VectorEngineManager

class AppContext(BaseModel):
    """
    Defines the complete, typed context for service layer operations.
    This acts as a "contract" for what dependencies are available and is
    the single source of truth for service dependencies.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    # 核心数据库会话
    db: AsyncSession
    
    # [KEY CHANGE] 完整的、可选的认证上下文
    # 对于需要认证的路由，它将是一个 AuthContext 实例；对于公共路由，它将是 None。
    auth: Optional[AuthContext] = None
    
    # 全局应用级服务/引擎
    redis_service: RedisService
    vector_manager: VectorEngineManager
    arq_pool: ArqRedis
    vector_cache: Optional[VectorCache] = None

    # [NEW] 添加便利的属性访问器，以确保安全访问
    @property
    def actor(self):
        if not self.auth or not self.auth.user:
            raise PermissionError("An authenticated user (actor) is required for this operation.")
        return self.auth.user

    @property
    def team(self):
        if not self.auth or not self.auth.team:
            raise PermissionError("An authenticated team is required for this operation.")
        return self.auth.team

    @property
    def billing_owner(self):
        if not self.auth or (not self.auth.user and not self.auth.team):
            raise PermissionError("An authenticated billing owner is required for this operation.")
        return self.auth.team if self.auth.team else self.auth.user

    @property
    def perm_evaluator(self) -> PermissionEvaluator:
        if not self.auth or not self.auth.evaluator:
            raise PermissionError("A permission evaluator is required for this operation.")
        return self.auth.evaluator