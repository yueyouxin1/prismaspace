from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional, Set
from app.core.context import AppContext
from app.dao.resource.execution_dao import ResourceExecutionDao
from app.models import ResourceExecution, ResourceExecutionStatus, ResourceInstance, User
from app.services.base_service import BaseService
from app.utils.id_generator import generate_uuid


class ExecutionLedgerService(BaseService):
    """
    轻量级执行台账服务。
    """

    def __init__(self, context: AppContext):
        self.context = context
        self.db = context.db
        self.dao = ResourceExecutionDao(self.db)

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(UTC).replace(tzinfo=None)

    async def create_execution(
        self,
        *,
        instance: ResourceInstance,
        actor: User,
        thread_id: str,
        parent_run_id: Optional[str] = None,
    ) -> ResourceExecution:
        run_id = generate_uuid()
        execution = ResourceExecution(
            run_id=run_id,
            thread_id=thread_id,
            parent_run_id=parent_run_id,
            resource_instance_id=instance.id,
            user_id=actor.id,
            status=ResourceExecutionStatus.PENDING,
        )
        self.db.add(execution)
        await self.db.flush()
        return execution

    async def get_by_run_id(self, run_id: str) -> Optional[ResourceExecution]:
        return await self.dao.get_by_run_id(run_id)

    async def get_latest_active_execution(
        self,
        *,
        instance: ResourceInstance,
        actor: User,
        thread_id: str,
    ) -> Optional[ResourceExecution]:
        return await self.dao.get_latest_active_by_instance_user_thread(
            resource_instance_id=instance.id,
            user_id=actor.id,
            thread_id=thread_id,
        )

    async def resolve_parent_execution(
        self,
        *,
        parent_run_id: str,
        instance: ResourceInstance,
        actor: User,
        thread_id: str,
    ) -> Optional[ResourceExecution]:
        parent_execution = await self.dao.get_by_run_id(parent_run_id)
        if not parent_execution:
            return None
        if parent_execution.resource_instance_id != instance.id or parent_execution.user_id != actor.id:
            return None
        if not parent_execution.thread_id or parent_execution.thread_id != thread_id:
            return None
        return parent_execution

    async def resolve_lineage_root_run_id(
        self,
        *,
        execution: ResourceExecution,
        instance: ResourceInstance,
        actor: User,
        thread_id: str,
    ) -> Optional[str]:
        current = execution
        visited: Set[str] = set()

        while current.parent_run_id:
            if current.run_id in visited:
                return None
            visited.add(current.run_id)
            parent_execution = await self.resolve_parent_execution(
                parent_run_id=current.parent_run_id,
                instance=instance,
                actor=actor,
                thread_id=thread_id,
            )
            if parent_execution is None:
                return None
            current = parent_execution

        return current.run_id

    async def mark_running(
        self,
        execution: ResourceExecution,
        *,
        trace_id: Optional[str] = None,
    ) -> None:
        execution.status = ResourceExecutionStatus.RUNNING
        if execution.started_at is None:
            execution.started_at = self._utcnow()
        if trace_id:
            execution.trace_id = trace_id
        await self.db.flush()

    async def mark_finished(
        self,
        execution: ResourceExecution,
        *,
        status: ResourceExecutionStatus,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        execution.status = status
        execution.error_code = error_code if status == ResourceExecutionStatus.FAILED else None
        execution.error_message = error_message if status == ResourceExecutionStatus.FAILED else None
        execution.finished_at = self._utcnow()
        await self.db.flush()
