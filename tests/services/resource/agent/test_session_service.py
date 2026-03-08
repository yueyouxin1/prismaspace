import uuid

import pytest

from app.dao.resource.resource_dao import ResourceInstanceDao
from app.models.resource.agent import AgentMessageRole
from app.services.resource.agent.session_service import AgentSessionService


@pytest.mark.asyncio
async def test_batch_append_messages_and_delete_message_track_turn_count(
    created_resource_factory,
    db_session,
    app_context_factory,
    registered_user_with_pro,
):
    resource = await created_resource_factory("agent")
    agent_instance = await ResourceInstanceDao(db_session).get_by_uuid(resource.workspace_instance.uuid)
    assert agent_instance is not None

    context = await app_context_factory(actor=registered_user_with_pro.user)
    service = AgentSessionService(context)
    session = await service.get_or_create_session(
        session_uuid=str(uuid.uuid4()),
        agent_instance=agent_instance,
        actor=registered_user_with_pro.user,
    )

    await service.batch_append_messages(
        session=session,
        messages_data=[
            {
                "message_uuid": "msg-turn-1-user",
                "role": AgentMessageRole.USER,
                "text_content": "hello",
                "run_id": "run-turn-1",
                "turn_id": "turn-1",
            },
            {
                "message_uuid": "msg-turn-1-assistant",
                "role": AgentMessageRole.ASSISTANT,
                "text_content": "world",
                "run_id": "run-turn-1",
                "turn_id": "turn-1",
            },
        ],
    )
    await db_session.refresh(session)

    assert session.message_count == 2
    assert session.turn_count == 1

    await service.batch_append_messages(
        session=session,
        messages_data=[
            {
                "message_uuid": "msg-turn-2-user",
                "role": AgentMessageRole.USER,
                "text_content": "next",
                "run_id": "run-turn-2",
                "turn_id": "turn-2",
            }
        ],
    )
    await db_session.refresh(session)

    assert session.message_count == 3
    assert session.turn_count == 2

    await service.delete_message("msg-turn-1-user", actor=registered_user_with_pro.user)
    await db_session.refresh(session)
    assert session.message_count == 2
    assert session.turn_count == 2

    await service.delete_message("msg-turn-1-assistant", actor=registered_user_with_pro.user)
    await db_session.refresh(session)
    assert session.message_count == 1
    assert session.turn_count == 1
