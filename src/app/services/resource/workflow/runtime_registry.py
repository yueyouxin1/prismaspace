import asyncio
from typing import Dict


class WorkflowTaskRegistry:
    """
    进程内运行中的 workflow task 索引。
    配合 Redis 取消信号实现“本进程立即取消 + 跨进程最终一致取消”。
    """

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
