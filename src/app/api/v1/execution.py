# src/app/api/v1/execution.py

from fastapi import APIRouter, Depends, Body, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any
from pydantic import BaseModel, Field
from app.core.context import AppContext
from app.api.dependencies.context import AuthContextDep
from app.schemas.common import JsonResponse
from app.schemas.resource.execution_schemas import AnyExecutionRequest, AnyExecutionResponse
from app.services.resource.execution.execution_service import ExecutionService
from app.services.exceptions import PermissionDeniedError, NotFoundError, ServiceException

router = APIRouter()

@router.post(
    "/instances/{instance_uuid}",
    response_model=JsonResponse[AnyExecutionResponse],
    summary="Execute a Resource Instance",
    description="Triggers the execution of any published, executable resource instance (e.g., a Tool)."
)
async def execute_instance_endpoint(
    instance_uuid: str,
    request_body: AnyExecutionRequest = Body(...),
    context: AppContext = AuthContextDep
):
    try:
        """
        统一的资源执行入口 API。
        """
        # 1. 实例化服务，传入所有必要的上下文
        execution_service = ExecutionService(context)
        
        # 2. 调用服务层的核心方法
        result_model = await execution_service.execute_instance(
            instance_uuid=instance_uuid,
            execute_params=request_body,
            actor=context.actor
        )
        
        # 3. 将服务层返回的纯净数据包装成标准 API 响应
        return JsonResponse(data=result_model)
    except PermissionDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ServiceException as e:
        raise HTTPException(status_code=400, detail=str(e))