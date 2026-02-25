from typing import List
from sqlalchemy import select
from app.worker.context import rebuild_context_for_worker
from app.services.resource.agent.memory.deep.long_term_context_service import LongTermContextService
from app.services.resource.agent.memory.deep.context_summary_service import ContextSummaryService
from app.dao.resource.agent.agent_dao import AgentDao
from app.models.interaction.chat import ChatMessage
from app.schemas.resource.agent.agent_schemas import AgentConfig

async def index_trace_task(ctx: dict, agent_instance_id: int, session_uuid: str, trace_id: str, runtime_workspace_id: int, user_uuid: str):
    """
    ARQ Task: 索引整个 Trace 轮次的消息。
    """
    try:
        db_session_factory = ctx['db_session_factory']
        async with db_session_factory() as session:
            async with session.begin():
                app_context = await rebuild_context_for_worker(ctx, session, user_uuid)
                
                # 1. 获取该 Trace 下的所有消息
                stmt = select(ChatMessage).where(ChatMessage.trace_id == trace_id)
                result = await session.execute(stmt)
                messages = result.scalars().all()
                
                if not messages: return

                # 2. 调用服务进行批量索引
                long_term_service = LongTermContextService(app_context)
                await long_term_service.index_turn_background(
                    agent_instance_id=agent_instance_id,
                    session_uuid=session_uuid,
                    trace_id=trace_id,
                    messages=messages,
                    runtime_workspace_id=runtime_workspace_id
                )
                
    except Exception as e:
        print(f"Failed to index trace {trace_id}: {e}")

async def summarize_trace_task(ctx: dict, agent_instance_id: int, session_uuid: str, trace_id: str, runtime_workspace_id: int, user_uuid: str):
    """
    ARQ Task: 对一轮 Trace 进行摘要压缩。
    """
    try:
        db_session_factory = ctx['db_session_factory']
        async with db_session_factory() as session:
            async with session.begin():
                app_context = await rebuild_context_for_worker(ctx, session, user_uuid)
                
                # 1. 获取消息
                stmt = select(ChatMessage).where(ChatMessage.trace_id == trace_id).order_by(ChatMessage.id.asc())
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
                    trace_id=trace_id,
                    messages=messages,
                    deep_memory_config=agent_config.deep_memory,
                    runtime_workspace_id=runtime_workspace_id
                )
                
    except Exception as e:
        print(f"Failed to summarize trace {trace_id}: {e}")
