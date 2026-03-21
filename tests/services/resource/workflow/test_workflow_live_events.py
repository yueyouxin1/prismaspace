from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.resource.workflow.live_events import WorkflowLiveEventService


pytestmark = pytest.mark.asyncio


async def test_get_buffered_events_reads_tail_window_after_sequence():
    class FakePipeline:
        def __init__(self):
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
                    '{"seq": 4, "payload": {"event": "stream.delta", "data": {"delta": "b"}}}',
                    '{"seq": 5, "payload": {"event": "run.finished", "data": {"output": {"ok": true}}}}',
                ]
            )

        def pipeline(self, transaction=False):
            return FakePipeline()

    redis_client = FakeRedisClient()
    service = WorkflowLiveEventService(SimpleNamespace(redis_service=SimpleNamespace(client=redis_client)))

    events = await service.get_buffered_events("run-1", after_seq=3)

    redis_client.lrange.assert_awaited_once_with(service.events_key("run-1"), 3, -1)
    assert [item["seq"] for item in events] == [4, 5]
