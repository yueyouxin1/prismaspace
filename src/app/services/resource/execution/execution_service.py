# src/app/services/resource/execution/execution_service.py

from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, List, Any, Optional
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.core.context import AppContext
from app.models import User, Team, Workspace, Product, Feature
from app.models.resource import Resource, ResourceInstance, VersionStatus
from app.dao.resource.resource_dao import ResourceInstanceDao
from app.services.resource.base.base_resource_service import BaseResourceService
from app.services.exceptions import NotFoundError, ServiceException
from app.schemas.resource.execution_schemas import AnyExecutionRequest, AnyExecutionResponse
from app.services.billing.interceptor import InsufficientFundsError
from app.core.config import settings

class ExecutionService(BaseResourceService):
    """
    统一的、有状态的执行编排入口。
    实现了“预估-冻结-结算”协议的编排与审计部分。
    """
    def __init__(self, context: AppContext):
        super().__init__(context)

    async def execute_instance(
        self, 
        instance_uuid: str, 
        execute_params: AnyExecutionRequest, 
        actor: User,
        runtime_workspace: Optional[Workspace] = None
    ) -> AnyExecutionResponse:

        try:
            # 分派给专家服务
            impl_service = await self._get_impl_service_by_instance(instance_uuid)

            # [MODIFIED] The `execute` signature now accepts the billing context
            return await impl_service.execute(
                instance_uuid=instance_uuid,
                execute_params=execute_params,
                actor=actor,
                runtime_workspace=runtime_workspace
            )

        except InsufficientFundsError as e:
            # 资金不足的异常会被 billing_context 正常捕获并传递上来
            # The billing context will automatically settle any successful reservations
            # that happened *before* the failure, which is CORRECT.
            raise InsufficientFundsError(f"Execution failed due to insufficient funds: {e}")

    async def execute_batch(
        self,
        instance_uuids: List[str],
        execute_params: AnyExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None
    ) -> AnyExecutionResponse:
        """
        [NEW] 批量执行入口。
        目前假设所有 UUID 属于同一种资源类型（例如都是 VectorDB）。
        """
        if not instance_uuids:
            return AnyExecutionResponse(data=[])

        # 1. 确定资源类型 (取第一个)
        # 在实际复杂场景中，可能需要分组处理不同类型的资源，但通常 Batch 操作针对同类资源
        first_uuid = instance_uuids[0]
        impl_service = await self._get_impl_service_by_instance(first_uuid)

        try:
            # 2. 调用具体的批量实现
            return await impl_service.execute_batch(
                instance_uuids=instance_uuids,
                execute_params=execute_params,
                actor=actor,
                runtime_workspace=runtime_workspace
            )

        except InsufficientFundsError as e:
            raise InsufficientFundsError(f"Batch execution failed due to insufficient funds: {e}")
