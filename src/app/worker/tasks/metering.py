import traceback
from app.worker.context import rebuild_context_for_worker
from app.services.billing.consumption_service import ConsumptionService

async def process_consumption_task(ctx: dict, record_id: int, user_uuid: str):
    """
    ARQ Worker 任务：处理单条 Trace 记录的记账。
    """

    try:
        db_session_factory = ctx['db_session_factory']
        # [关键] 每个任务都应该在自己的会话和事务中运行
        async with db_session_factory() as session:
            async with session.begin():
                app_context = await rebuild_context_for_worker(ctx, session, user_uuid=user_uuid)
                
                consumption_service = ConsumptionService(app_context)
                
                # 在独立的会话和事务中执行记账
                await consumption_service.process_record(record_id)

    except Exception as e:
        # [调试核心] Worker 级别的最终捕获，将详细错误信息写入 Redis
        error_message = f"FATAL in task process_consumption_task for record_id: {record_id}\n"
        error_message += f"Error Type: {type(e).__name__}\n"
        error_message += f"Error: {e}\n"
        error_message += "Traceback:\n" + traceback.format_exc()
        
        # 打印到 worker 日志，以防万一
        print(error_message)
        
        raise # 仍然重新抛出，让 ARQ 知道任务失败了