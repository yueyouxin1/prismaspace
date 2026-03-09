import logging

from app.schemas.resource.workflow.workflow_schemas import WorkflowExecutionRequest
from app.worker.context import rebuild_context_for_worker


logger = logging.getLogger(__name__)


async def execute_workflow_run_task(
    ctx: dict,
    run_id: str,
    instance_uuid: str,
    actor_uuid: str,
    execute_params: dict,
):
    """
    ARQ Task: 执行预先创建好的 workflow run。
    """
    try:
        db_session_factory = ctx["db_session_factory"]
        async with db_session_factory() as session:
            async with session.begin():
                app_context = await rebuild_context_for_worker(ctx, session, actor_uuid)
                from app.services.resource.workflow.workflow_service import WorkflowService

                service = WorkflowService(app_context)
                request = WorkflowExecutionRequest.model_validate(execute_params)
                await service.execute_precreated_run(
                    run_id=run_id,
                    instance_uuid=instance_uuid,
                    execute_params=request,
                    actor=app_context.actor,
                )
    except Exception:
        logger.exception("Failed to execute workflow run %s", run_id)
        raise
