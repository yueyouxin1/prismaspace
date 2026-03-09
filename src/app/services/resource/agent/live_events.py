from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any, AsyncGenerator, Dict, List

from app.core.context import AppContext
from app.services.base_service import BaseService


class AgentLiveEventService(BaseService):
    EVENTS_TTL = timedelta(hours=24)
    TERMINAL_TTL = timedelta(minutes=15)
    MAX_BUFFERED_EVENTS = 2000
    POLL_INTERVAL_SECONDS = 0.2

    def __init__(self, context: AppContext):
        self.context = context
        self.redis = context.redis_service

    @staticmethod
    def events_key(run_id: str) -> str:
        return f"agent:run:{run_id}:live:events"

    @staticmethod
    def seq_key(run_id: str) -> str:
        return f"agent:run:{run_id}:live:seq"

    @staticmethod
    def meta_key(run_id: str) -> str:
        return f"agent:run:{run_id}:live:meta"

    async def record_event(self, run_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        seq = await self.redis.client.incr(self.seq_key(run_id))
        envelope = {"seq": seq, "payload": payload}
        key = self.events_key(run_id)
        await self.redis.client.rpush(key, json.dumps(envelope, ensure_ascii=False))
        await self.redis.client.ltrim(key, -self.MAX_BUFFERED_EVENTS, -1)

        event_type = str(payload.get("type", ""))
        terminal = event_type in {"RUN_FINISHED", "RUN_ERROR"}
        ttl = self.TERMINAL_TTL if terminal else self.EVENTS_TTL
        await self.redis.client.expire(key, int(ttl.total_seconds()))
        await self.redis.client.expire(self.seq_key(run_id), int(ttl.total_seconds()))
        await self.redis.set_json(
            self.meta_key(run_id),
            {"terminal": terminal, "last_seq": seq},
            expire=ttl,
        )
        return envelope

    async def get_buffered_events(self, run_id: str, *, after_seq: int = 0) -> List[Dict[str, Any]]:
        raw_items = await self.redis.client.lrange(self.events_key(run_id), 0, -1)
        events: List[Dict[str, Any]] = []
        for item in raw_items:
            try:
                payload = json.loads(item)
            except Exception:
                continue
            seq = int(payload.get("seq", 0))
            if seq > after_seq:
                events.append(payload)
        return events

    async def stream_events(self, run_id: str, *, after_seq: int = 0) -> AsyncGenerator[Dict[str, Any], None]:
        current_seq = after_seq
        while True:
            events = await self.get_buffered_events(run_id, after_seq=current_seq)
            for event in events:
                current_seq = max(current_seq, int(event.get("seq", current_seq)))
                yield event

            meta = await self.redis.get_json(self.meta_key(run_id)) or {}
            if meta.get("terminal") and not events:
                return

            await asyncio.sleep(self.POLL_INTERVAL_SECONDS)
