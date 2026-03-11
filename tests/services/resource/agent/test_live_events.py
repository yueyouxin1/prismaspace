from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.resource.agent.live_events import AgentLiveEventService


pytestmark = pytest.mark.asyncio


async def test_live_event_buffer_does_not_touch_redis_while_attached():
    service = AgentLiveEventService(SimpleNamespace(redis_service=SimpleNamespace()))
    service.append_event_batch = AsyncMock()

    buffer = service.create_buffer("run-1")
    await buffer.publish({"type": "RUN_STARTED", "runId": "run-1"})
    await buffer.publish({"type": "TEXT_MESSAGE_CONTENT", "delta": "hello"})
    await buffer.aclose()

    service.append_event_batch.assert_not_awaited()


async def test_live_event_buffer_flushes_buffered_and_future_events_after_detach():
    service = AgentLiveEventService(SimpleNamespace(redis_service=SimpleNamespace()))
    service.append_event_batch = AsyncMock()

    buffer = service.create_buffer("run-1")
    buffer.FLUSH_BATCH_SIZE = 2

    await buffer.publish({"type": "RUN_STARTED", "runId": "run-1"})
    await buffer.publish({"type": "TEXT_MESSAGE_CONTENT", "delta": "a"})

    buffer.detach()

    await buffer.publish({"type": "TEXT_MESSAGE_CONTENT", "delta": "b"})
    await buffer.publish({"type": "RUN_FINISHED", "runId": "run-1"})
    await buffer.aclose()

    flushed_envelopes = []
    for call in service.append_event_batch.await_args_list:
        assert call.args[0] == "run-1"
        flushed_envelopes.extend(call.args[1])

    assert [item["seq"] for item in flushed_envelopes] == [1, 2, 3, 4]
    assert flushed_envelopes[-1]["payload"]["type"] == "RUN_FINISHED"
