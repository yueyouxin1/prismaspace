# app/services/resource/base/base_resource_service.py

from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any, Type
from app.core.context import AppContext
from .common import CommonResourceService
from .base_impl_service import ResourceImplementationService, ALL_RESOURCE_IMPLE_SERVICE
from app.models.resource import ResourceInstance
from app.services.exceptions import ServiceException, NotFoundError

class BaseResourceService(CommonResourceService):
    def __init__(self, context: AppContext):
        super().__init__(context)
        # 为不同维度的缓存使用不同的字典
        self._instance_services_cache: Dict[str, ResourceImplementationService] = {}
        self._type_services_cache: Dict[str, ResourceImplementationService] = {}

    def _create_impl_service(self, resource_type: str) -> ResourceImplementationService:
        """
        [职责：创建服务实例]
        一个纯粹的、无 I/O 的工厂方法，只负责从注册表中找到对应的服务类并实例化。
        """
        service_class = ALL_RESOURCE_IMPLE_SERVICE.get(resource_type)
        if not service_class:
            raise ServiceException(f"No implementation service registered for resource type '{resource_type}'.")
        
        print(f"Instantiating service for resource type: '{resource_type}'") # 用于调试
        return service_class(self.context)

    async def _get_impl_service_by_instance(
        self, 
        instance_or_uuid: str | ResourceInstance
    ) -> ResourceImplementationService:
        """
        [职责：管理实例隔离的服务生命周期]
        基于 instance_uuid 缓存，确保每个资源实例拥有独立的服务对象，实现状态隔离。
        例如：调用者执行领域资源有状态的运行时业务逻辑（如agent_run）
        """
        # 1. 无论输入是什么，先提取出 uuid
        is_instance = isinstance(instance_or_uuid, ResourceInstance)
        instance_uuid = instance_or_uuid.uuid if is_instance else instance_or_uuid

        # 2. 立即检查缓存（快速路径）
        if instance_uuid in self._instance_services_cache:
            return self._instance_services_cache[instance_uuid]

        # --- 缓存未命中，进入创建逻辑（慢速路径）---

        # 3. 确定 resource_type
        resource_type = instance_or_uuid.resource_type if is_instance else await self.instance_dao.get_type_by_uuid(instance_uuid)

        # 4. 创建、缓存并返回服务
        impl_service = self._create_impl_service(resource_type)
        self._instance_services_cache[instance_uuid] = impl_service
        return impl_service

    async def _get_impl_service_by_type(self, resource_type: str) -> ResourceImplementationService:
        """
        [职责：管理类型共享的服务生命周期]
        基于 resource_type 缓存，确保同类型资源共享同一个服务对象，提高复用性。
        例如：调用者需要领域资源的通用方法（如CURD）进行批量操作
        """
        # 1. 检查类型缓存
        if resource_type in self._type_services_cache:
            return self._type_services_cache[resource_type]

        # 2. 创建服务
        impl_service = self._create_impl_service(resource_type)
        
        # 3. 存入类型缓存
        self._type_services_cache[resource_type] = impl_service
        return impl_service

    async def _get_instance_stub_by_uuid(self, instance_uuid: str) -> ResourceInstance:
        """
        获取轻量实例（用于状态、类型、权限链路判断）。
        """
        instance = await self.instance_dao.get_by_uuid(instance_uuid)
        if not instance:
            raise NotFoundError("Resource instance not found.")
        return instance

    async def _get_full_instance_by_uuid(
        self,
        instance_uuid: str,
        instance_stub: ResourceInstance | None = None
    ) -> ResourceInstance:
        """
        按实例类型分发到具体实现服务，加载完整子类实例。
        """
        stub = instance_stub or await self._get_instance_stub_by_uuid(instance_uuid)
        impl_service = await self._get_impl_service_by_type(stub.resource_type)
        full_instance = await impl_service.get_by_uuid(stub.uuid)
        if not full_instance:
            raise NotFoundError("Resource instance not found.")
        return full_instance
