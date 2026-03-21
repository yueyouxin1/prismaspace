from __future__ import annotations

import asyncio
import json
from collections import deque
from datetime import timedelta
from typing import Any, AsyncGenerator, Deque, Dict, List, Optional

from app.core.context import AppContext
from app.services.base_service import BaseService


class WorkflowLiveEventBuffer:
    """
    Workflow live event buffer.
    Attached 阶段只保留进程内缓冲；detach 后异步刷入 Redis，
    供 run_id 级 live attach / reconnect 使用。
    """

    FLUSH_BATCH_SIZE = 64

    def __init__(self, service: "WorkflowLiveEventService", run_id: str):
        self.service = service
        self.run_id = run_id
        self._events: Deque[Dict[str, Any]] = deque(maxlen=service.MAX_BUFFERED_EVENTS)
        self._next_seq = 1
        self._detached = False
        self._closed = False
        self._flush_queue: asyncio.Queue[Optional[Dict[str, Any]]] = asyncio.Queue()
        self._flush_task: Optional[asyncio.Task[None]] = None

    async def publish(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        envelope = {"seq": self._next_seq, "payload": payload}
        self._next_seq += 1
        self._events.append(envelope)
        if self._detached:
            await self._flush_queue.put(envelope)
        return envelope

    def detach(self) -> None:
        if self._detached:
            return
        self._detached = True
        self._flush_task = asyncio.create_task(self._flush_loop())
        for envelope in list(self._events):
            self._flush_queue.put_nowait(envelope)
        if self._closed:
            self._flush_queue.put_nowait(None)

    async def mark_terminal(self) -> None:
        await self.service.mark_terminal(self.run_id, last_seq=self._next_seq - 1)

    async def aclose(self) -> None:
        self._closed = True
        if not self._detached:
            return
        await self._flush_queue.put(None)
        if self._flush_task is not None:
            await self._flush_task

    async def _flush_loop(self) -> None:
        while True:
            item = await self._flush_queue.get()
            if item is None:
                return

            batch = [item]
            reached_end = False
            while len(batch) < self.FLUSH_BATCH_SIZE:
                try:
                    next_item = self._flush_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if next_item is None:
                    reached_end = True
                    break
                batch.append(next_item)

            await self.service.append_event_batch(self.run_id, batch)

            if reached_end:
                return


class WorkflowLiveEventService(BaseService):
    EVENTS_TTL = timedelta(hours=24)
    TERMINAL_TTL = timedelta(minutes=15)
    MAX_BUFFERED_EVENTS = 2000
    POLL_INTERVAL_SECONDS = 0.2
    TERMINAL_EVENTS = {"run.finished", "run.failed", "run.cancelled", "run.interrupted", "system.error"}

    def __init__(self, context: AppContext):
        self.context = context
        self.redis = context.redis_service

    @staticmethod
    def events_key(run_id: str) -> str:
        return f"workflow:run:{run_id}:live:events"

    @staticmethod
    def seq_key(run_id: str) -> str:
        return f"workflow:run:{run_id}:live:seq"

    @staticmethod
    def meta_key(run_id: str) -> str:
        return f"workflow:run:{run_id}:live:meta"

    def create_buffer(self, run_id: str) -> WorkflowLiveEventBuffer:
        return WorkflowLiveEventBuffer(self, run_id)

    @classmethod
    def _is_terminal(cls, payload: Dict[str, Any]) -> bool:
        return str(payload.get("event", "")) in cls.TERMINAL_EVENTS

    async def mark_terminal(self, run_id: str, *, last_seq: int) -> None:
        ttl_seconds = int(self.TERMINAL_TTL.total_seconds())
        await self.redis.set_json(
            self.meta_key(run_id),
            {"terminal": True, "last_seq": int(last_seq)},
            expire=ttl_seconds,
        )
        await self.redis.client.set(
            self.seq_key(run_id),
            str(int(last_seq)),
            ex=ttl_seconds,
        )
        await self.redis.client.expire(self.events_key(run_id), ttl_seconds)

    async def append_event_batch(self, run_id: str, envelopes: List[Dict[str, Any]]) -> None:
        if not envelopes:
            return

        key = self.events_key(run_id)
        last_seq = int(envelopes[-1]["seq"])
        terminal = any(self._is_terminal(item.get("payload", {})) for item in envelopes)
        ttl = self.TERMINAL_TTL if terminal else self.EVENTS_TTL
        serialized = [json.dumps(item, ensure_ascii=False) for item in envelopes]

        async with self.redis.client.pipeline(transaction=False) as pipe:
            pipe.rpush(key, *serialized)
            pipe.ltrim(key, -self.MAX_BUFFERED_EVENTS, -1)
            pipe.expire(key, int(ttl.total_seconds()))
            pipe.set(self.seq_key(run_id), str(last_seq), ex=int(ttl.total_seconds()))
            pipe.set(
                self.meta_key(run_id),
                json.dumps({"terminal": terminal, "last_seq": last_seq}, ensure_ascii=False),
                ex=int(ttl.total_seconds()),
            )
            await pipe.execute()

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
