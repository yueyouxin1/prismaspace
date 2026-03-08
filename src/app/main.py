import re
import os
import sys
import time
import traceback
import json
import asyncio
import argparse
from io import BytesIO
from typing import List, Generator
import redis.asyncio as redis
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, BackgroundTasks, Request, Depends, status, HTTPException, Response, UploadFile, File
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi import WebSocket, WebSocketDisconnect
from starlette.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from app.db.session import SessionLocal
from app.db.base import Base
from app.core.config import settings
from app.api.router import router
from app.services.redis_service import RedisService
from app.services.permission.hierarchy import preload_permission_hierarchy
from app.services.billing.interceptor import InsufficientFundsError
from app.services.exceptions import ServiceException, PermissionDeniedError
from app.system.vectordb.manager import SystemVectorManager
from app.system.resource.workflow.node_def_manager import NodeDefManager
from app.engine.model.llm import LLMEngineService
from app.engine.vector.main import VectorEngineManager, VectorEngineConfig
from app.middleware import AuthenticationMiddleware
from app.observability import PerformanceObservabilityMiddleware, PyInstrumentProfilingMiddleware
from app.schemas.common import JsonResponse, JsonFaildResponse

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- [关键] Redis 连接池生命周期 ---
    # 这个连接池将由 FastAPI Web 进程和 ARQ 客户端共享
    print("Connecting to Redis...")
    redis_settings = RedisSettings(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        password=settings.REDIS_PASSWORD,
        database=settings.REDIS_DB
    )
    
    # 创建一个 redis 客户端，用于我们的 RedisService (缓存)
    app.state.redis_service = RedisService()
    await app.state.redis_service.initialize()
    
    # 创建一个 ARQ 客户端连接池，用于向队列发送任务
    app.state.arq_pool = await create_pool(redis_settings)

    # --- Vector Engine 生命周期管理 ---
    # 1. 从 settings.py 构建配置对象
    engine_configs = [VectorEngineConfig(**config_dict) for config_dict in settings.VECTOR_ENGINE_CONFIGS]
    # 2. 初始化管理器并存储在 app.state 中
    vector_manager = VectorEngineManager(configs=engine_configs)
    await vector_manager.startup()
    app.state.vector_manager = vector_manager

    async with SessionLocal() as db_session:
        app.state.permission_hierarchy = await preload_permission_hierarchy(db_session)
        sys_vector_mgr = SystemVectorManager(db_session, app.state.vector_manager)
        # 1. 确保所有应有的集合存在 (幂等)
        await sys_vector_mgr.initialize_system_collections()
        # 2. 清理开发过程中产生的垃圾 (Optional, can be disabled in PROD for safety if preferred)
        # 建议在开发/测试环境开启，生产环境慎用或手动触发
        if settings.APP_ENV != "production":
            await sys_vector_mgr.prune_orphan_collections()

        # 同步 Workflow 节点定义（幂等 Upsert）
        await NodeDefManager(db_session).sync_nodes()

    yield
    
    # --- 清理 ---
    print("Closing Redis connections...")
    await LLMEngineService.close_cached_clients()
    await app.state.redis_service.close()
    await app.state.arq_pool.aclose()
    await app.state.vector_manager.shutdown()

app = FastAPI(
    host=settings.APP_HOST, 
    port=settings.APP_PORT,
    lifespan=lifespan
)

app.mount("/public", StaticFiles(directory="public"), name="public")

app.add_middleware(AuthenticationMiddleware)

#设置允许访问的域名
origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  #设置允许的origins来源
    allow_credentials=True,
    allow_methods=["*"],  # 设置允许跨域的http方法，比如 get、post、put等。
    allow_headers=["*"])  #允许跨域的headers，可以用来鉴别来源等作用。

if settings.PERF_OBSERVABILITY_ENABLED:
    app.add_middleware(PerformanceObservabilityMiddleware)

if settings.PYINSTRUMENT_PROFILING_ENABLED:
    app.add_middleware(PyInstrumentProfilingMiddleware)

app.include_router(router)

@app.exception_handler(InsufficientFundsError)
async def insufficient_funds_exception_handler(request: Request, exc: InsufficientFundsError):
    """
    专门处理无法计费的异常，并返回 402 Forbidden。
    """
    return JSONResponse(
        status_code=status.HTTP_402_FORBIDDEN,
        content={"status": status.HTTP_402_FORBIDDEN, "msg": exc.message, "data": None},
    )
    
@app.exception_handler(PermissionDeniedError)
async def permission_denied_exception_handler(request: Request, exc: PermissionDeniedError):
    """
    专门处理权限不足的异常，并返回 403 Forbidden。
    """
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={"status": status.HTTP_403_FORBIDDEN, "msg": exc.message, "data": None},
    )

@app.exception_handler(ServiceException)
async def service_exception_handler(request: Request, exc: ServiceException):
    # 处理所有来自服务层的、可预期的业务逻辑错误
    traceback.print_exc()
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"status": status.HTTP_400_BAD_REQUEST, "msg": exc.message, "data": None},
    )

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # 重写 FastAPI 默认的 HTTPException 处理器，以匹配我们的响应格式
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": exc.status_code, "msg": exc.detail, "data": None},
        headers=exc.headers,
    )

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    # 这个处理器现在只处理真正未预料到的服务器内部错误
    traceback.print_exc() # 在生产中应使用 logging
    return JSONResponse(
        status_code=500,
        content={"status": 500, "msg": "Internal Server Error", "data": None},
    )
