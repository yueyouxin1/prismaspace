import uuid

import pytest

from app.dao.resource.resource_dao import ResourceInstanceDao
from app.models.resource.agent import AgentMessageRole
from app.schemas.resource.agent.agent_schemas import DeepMemoryConfig
from app.services.resource.agent.agent_session_manager import AgentSessionManager


pytestmark = pytest.mark.asyncio


async def test_agent_session_manager_defers_deep_memory_jobs_until_post_commit_dispatch(
    created_resource_factory,
    db_session,
    app_context_factory,
    registered_user_with_pro,
):
    resource = await created_resource_factory("agent")
    agent_instance = await ResourceInstanceDao(db_session).get_by_uuid(resource.workspace_instance.uuid)
    assert agent_instance is not None

    context = await app_context_factory(actor=registered_user_with_pro.user)
    session_manager = AgentSessionManager(
        context=context,
        session_uuid=str(uuid.uuid4()),
        run_id="run-1",
        turn_id="turn-1",
        trace_id="trace-1",
        agent_instance=agent_instance,
        runtime_workspace=registered_user_with_pro.personal_workspace,
        actor=registered_user_with_pro.user,
        create_if_missing=True,
    )
    await session_manager.initialize()

    session_manager.buffer_message(
        role=AgentMessageRole.USER,
        text_content="hello",
    )
    session_manager.buffer_message(
        role=AgentMessageRole.ASSISTANT,
        text_content="world",
    )

    deep_memory = DeepMemoryConfig(
        enabled=True,
        enable_vector_recall=True,
        enable_summarization=True,
    )

    await session_manager.commit(deep_memory)

    context.arq_pool.enqueue_job.assert_not_awaited()

    await session_manager.dispatch_post_commit_jobs()

    dispatched_jobs = [call.args[0] for call in context.arq_pool.enqueue_job.await_args_list]
    assert dispatched_jobs == ["index_turn_task", "summarize_turn_task"]
