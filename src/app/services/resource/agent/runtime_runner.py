import asyncio
import logging
from typing import Optional

from app.core.context import AppContext
from app.models import User, Workspace
from app.schemas.protocol import RunAgentInputExt
from app.services.resource.agent.types.agent import AgentRunResult


logger = logging.getLogger(__name__)


class AgentRuntimeRunner:
    """
    使用独立 runtime session 承载单次 Agent 运行的完整生命周期。
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
        run_input: RunAgentInputExt,
        actor: User,
        runtime_workspace: Optional[Workspace] = None,
    ) -> AgentRunResult:
        loop = asyncio.get_running_loop()
        prepared_future: asyncio.Future[AgentRunResult] = loop.create_future()

        async def _run() -> None:
            from app.services.resource.agent.agent_service import AgentService

            async with self.db_session_factory() as runtime_db:
                runtime_context = self._build_runtime_context(runtime_db)
                runtime_service = AgentService(runtime_context)
                try:
                    prepared = await runtime_service._prepare_async_run(
                        instance_uuid=instance_uuid,
                        run_input=run_input,
                        actor=actor,
                        runtime_workspace=runtime_workspace,
                    )
                except Exception as exc:
                    if not prepared_future.done():
                        prepared_future.set_exception(exc)
                    return

                run_result = prepared.result
                if not prepared_future.done():
                    prepared_future.set_result(run_result)

                await runtime_service._run_agent_background_task(**prepared.background_task_kwargs)

        runner_task = asyncio.create_task(_run())
        try:
            run_result = await prepared_future
        except Exception:
            if not runner_task.done():
                runner_task.cancel()
            raise

        run_result.cancel = lambda: (not runner_task.done()) and runner_task.cancel()
        return run_result
