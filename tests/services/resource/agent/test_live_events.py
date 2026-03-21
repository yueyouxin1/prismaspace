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


async def test_get_buffered_events_reads_tail_window_after_sequence():
    class FakePipeline:
        def __init__(self, client):
            self.client = client
            self.ops = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

        def get(self, key):
            self.ops.append(("get", key))

        def llen(self, key):
            self.ops.append(("llen", key))

        async def execute(self):
            return ["5", 5]

    class FakeRedisClient:
        def __init__(self):
            self.lrange = AsyncMock(
                return_value=[
                    '{"seq": 4, "payload": {"type": "TEXT_MESSAGE_CONTENT", "delta": "b"}}',
                    '{"seq": 5, "payload": {"type": "RUN_FINISHED", "runId": "run-1"}}',
                ]
            )

        def pipeline(self, transaction=False):
            return FakePipeline(self)

    redis_client = FakeRedisClient()
    service = AgentLiveEventService(SimpleNamespace(redis_service=SimpleNamespace(client=redis_client)))

    events = await service.get_buffered_events("run-1", after_seq=3)

    redis_client.lrange.assert_awaited_once_with(service.events_key("run-1"), 3, -1)
    assert [item["seq"] for item in events] == [4, 5]
