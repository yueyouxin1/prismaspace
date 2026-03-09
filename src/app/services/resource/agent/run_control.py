import asyncio
from datetime import timedelta
from typing import Dict

from app.core.context import AppContext


class AgentRunRegistry:
    _tasks: Dict[str, asyncio.Task] = {}

    @classmethod
    def register(cls, run_id: str, task: asyncio.Task) -> None:
        cls._tasks[run_id] = task

    @classmethod
    def unregister(cls, run_id: str) -> None:
        cls._tasks.pop(run_id, None)

    @classmethod
    def cancel(cls, run_id: str) -> bool:
        task = cls._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True


class AgentRunControlService:
    CANCEL_SIGNAL_TTL = timedelta(hours=24)

    def __init__(self, context: AppContext):
        self.context = context

    @staticmethod
    def cancel_signal_key(run_id: str) -> str:
        return f"agent:run:{run_id}:cancel"

    async def request_cancel(self, run_id: str) -> None:
        await self.context.redis_service.set_json(
            self.cancel_signal_key(run_id),
            {"requested": True},
            expire=self.CANCEL_SIGNAL_TTL,
        )

    async def clear_cancel(self, run_id: str) -> None:
        await self.context.redis_service.delete_key(self.cancel_signal_key(run_id))

    async def should_cancel(self, run_id: str) -> bool:
        payload = await self.context.redis_service.get_json(self.cancel_signal_key(run_id))
        return payload is not None
