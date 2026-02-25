# app/services/resource/base/common.py

from typing import Dict, Any
from app.core.context import AppContext
from app.services.base_service import BaseService
from app.models.resource import Resource, ResourceInstance, VersionStatus
from app.dao.resource.resource_dao import ResourceInstanceDao
from app.services.exceptions import ServiceException

class CommonResourceService(BaseService):
    def __init__(self, context: AppContext):
        self.context = context
        self.db = context.db
        self.instance_dao = ResourceInstanceDao(context.db)

    async def _check_execute_perm(self, instance: ResourceInstance) -> ResourceInstance:
        workspace = instance.resource.workspace
        perm_target = self.context.actor if instance.visibility == 'public' else workspace
        await self.context.perm_evaluator.ensure_can(["resource:execute"], target=perm_target)
        if instance.status not in [VersionStatus.PUBLISHED, VersionStatus.WORKSPACE]:
            raise ServiceException("Only published or workspace instances can be executed.")
