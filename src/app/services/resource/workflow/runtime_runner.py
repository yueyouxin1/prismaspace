import asyncio
import logging
from typing import Optional

from app.core.context import AppContext
from app.models import User, Workspace
from app.schemas.resource.workflow.workflow_schemas import WorkflowExecutionRequest
from app.services.resource.workflow.runtime_registry import WorkflowTaskRegistry
from app.services.resource.workflow.types.workflow import WorkflowRunResult


logger = logging.getLogger(__name__)


class WorkflowRuntimeRunner:
    """
    为 workflow 执行提供独立 runtime DB session。
    与 Agent runtime runner 一致，避免长时流式执行绑死请求级 session。
    """

    def __init__(self, base_context: AppContext, db_session_factory):
        self.base_context = base_context
        self.db_session_factory = db_session_factory

    def _build_runtime_context(self, runtime_db) -> AppContext:
        return AppContext(
            db=runtime_db,
            db_session_factory=self.db_session_factory,
            auth=self.base_context.auth,
            redis_service=self.base_context.redis_service,
            vector_manager=self.base_context.vector_manager,
            arq_pool=self.base_context.arq_pool,
            vector_cache=self.base_context.vector_cache,
        )

    async def start(
        self,
        *,
        instance_uuid: str,
        execute_params: WorkflowExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None,
    ) -> WorkflowRunResult:
        loop = asyncio.get_running_loop()
        prepared_future: asyncio.Future[WorkflowRunResult] = loop.create_future()

        async def _run() -> None:
            from app.services.resource.workflow.workflow_service import WorkflowService

            async with self.db_session_factory() as runtime_db:
                runtime_context = self._build_runtime_context(runtime_db)
                runtime_service = WorkflowService(runtime_context)
                try:
                    prepared = await runtime_service._prepare_async_run(
                        instance_uuid=instance_uuid,
                        execute_params=execute_params,
                        actor=actor,
                        runtime_workspace=runtime_workspace,
                    )
                except Exception as exc:
                    if not prepared_future.done():
                        prepared_future.set_exception(exc)
                    return

                run_result = prepared.result
                WorkflowTaskRegistry.register(run_result.run_id, asyncio.current_task())
                if not prepared_future.done():
                    prepared_future.set_result(run_result)

                try:
                    await runtime_service._run_workflow_background_task(**prepared.background_task_kwargs)
                finally:
                    WorkflowTaskRegistry.unregister(run_result.run_id)

        runner_task = asyncio.create_task(_run())
        try:
            run_result = await prepared_future
        except Exception:
            if not runner_task.done():
                runner_task.cancel()
            raise

        run_result.task = runner_task
        run_result.cancel = lambda: (not runner_task.done()) and runner_task.cancel()
        return run_result
