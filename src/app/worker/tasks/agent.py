import logging

from sqlalchemy import select
from app.worker.context import rebuild_context_for_worker
from app.services.resource.agent.memory.deep.long_term_context_service import LongTermContextService
from app.services.resource.agent.memory.deep.context_summary_service import ContextSummaryService
from app.dao.resource.agent.agent_dao import AgentDao
from app.models.resource.agent import AgentMessage, AgentSession
from app.schemas.resource.agent.agent_schemas import AgentConfig

logger = logging.getLogger(__name__)

async def index_turn_task(
    ctx: dict,
    agent_instance_id: int,
    session_uuid: str,
    run_id: str,
    turn_id: str,
    runtime_workspace_id: int,
    user_uuid: str,
    trace_id: str | None = None,
):
    """
    ARQ Task: 索引整个业务轮次的消息。
    """
    try:
        db_session_factory = ctx['db_session_factory']
        async with db_session_factory() as session:
            async with session.begin():
                app_context = await rebuild_context_for_worker(ctx, session, user_uuid)
                
                # 1. 获取该业务轮次下的所有消息
                stmt = (
                    select(AgentMessage)
                    .join(AgentSession, AgentSession.id == AgentMessage.session_id)
                    .where(
                        AgentMessage.turn_id == turn_id,
                        AgentMessage.is_deleted == False,
                        AgentSession.uuid == session_uuid,
                        AgentSession.agent_instance_id == agent_instance_id,
                    )
                    .order_by(AgentMessage.id.asc())
                )
                result = await session.execute(stmt)
                messages = result.scalars().all()
                
                if not messages: return

                # 2. 调用服务进行批量索引
                long_term_service = LongTermContextService(app_context)
                await long_term_service.index_turn_background(
                    agent_instance_id=agent_instance_id,
                    session_uuid=session_uuid,
                    run_id=run_id,
                    turn_id=turn_id,
                    messages=messages,
                    runtime_workspace_id=runtime_workspace_id,
                    trace_id=trace_id,
                )
                
    except Exception:
        logger.exception("Failed to index turn %s", turn_id)
        raise

async def summarize_turn_task(
    ctx: dict,
    agent_instance_id: int,
    session_uuid: str,
    run_id: str,
    turn_id: str,
    runtime_workspace_id: int,
    user_uuid: str,
    trace_id: str | None = None,
):
    """
    ARQ Task: 对一轮业务轮次进行摘要压缩。
    """
    try:
        db_session_factory = ctx['db_session_factory']
        async with db_session_factory() as session:
            async with session.begin():
                app_context = await rebuild_context_for_worker(ctx, session, user_uuid)
                
                # 1. 获取消息
                stmt = (
                    select(AgentMessage)
                    .join(AgentSession, AgentSession.id == AgentMessage.session_id)
                    .where(
                        AgentMessage.turn_id == turn_id,
                        AgentMessage.is_deleted == False,
                        AgentSession.uuid == session_uuid,
                        AgentSession.agent_instance_id == agent_instance_id,
                    )
                    .order_by(AgentMessage.id.asc())
                )
                result = await session.execute(stmt)
                messages = result.scalars().all()
                if not messages: return

                # 2. 获取配置
                agent_dao = AgentDao(session)
                agent_instance = await agent_dao.get_by_pk(agent_instance_id)
                if not agent_instance: return
                
                try:
                    agent_config = AgentConfig(**agent_instance.agent_config)
                except:
                    return # 配置无效

                # 3. 调用服务
                summary_service = ContextSummaryService(app_context)
                
                await summary_service.summarize_turn_background(
                    agent_instance_id=agent_instance_id,
                    session_uuid=session_uuid,
                    run_id=run_id,
                    turn_id=turn_id,
                    messages=messages,
                    deep_memory_config=agent_config.deep_memory,
                    runtime_workspace_id=runtime_workspace_id,
                    trace_id=trace_id,
                )
                
    except Exception:
        logger.exception("Failed to summarize turn %s", turn_id)
        raise
