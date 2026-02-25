import asyncio
from typing import Optional, List, Dict, Any, AsyncGenerator
from .base import Streamable

class StreamBroadcaster(Streamable):
    """
    通用异步流广播器。
    允许多个消费者订阅同一个数据流，并在流结束时触发回调。
    """
    def __init__(
        self, 
        id: str
    ):
        self._id = id
        self._task = None
        self._queues = []
        self._history = []
        self._history_lock = asyncio.Lock()
        self._sentinel = object()
        self._stopped = False

    async def _task_wrapper(self, task_coro):
        """包装流式任务，确保完成后发出信号。"""
        try:
            # 执行实际的流式处理协程
            return await task_coro
        except Exception as e:
            print(f"[StreamBroadcaster] Error in task for {self._id}: {e}")
            await self.broadcast({"type": "error", "data": str(e)})
            raise e
        finally:
            # 向所有监听者发送停止信号
            async with self._history_lock: # 确保在发送Stop前不再有新数据进入
                self._stopped = True
                for q in self._queues:
                    await q.put(self._sentinel)

    def create_task(self, task_coro) -> asyncio.Task:
        """启动后台流式任务"""
        self._task = asyncio.create_task(self._task_wrapper(task_coro))
        return self._task

    def get_task(self) -> Optional[asyncio.Task]:
        return self._task

    async def get_result(self) -> Any:
        """
        直接利用 Task 作为 Future 的特性。
        """
        if not self._task:
            raise RuntimeError("Task not started")
        # 直接等待 Task，它处理了 Result 返回和 Exception 抛出
        return await self._task

    async def cancel(self):
        """允许外部取消内部任务"""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def broadcast(self, chunk: Any):
        """广播数据块。"""
        async with self._history_lock:
            self._history.append(chunk)
            for q in self._queues:
                await q.put(chunk)

    def subscribe(self) -> AsyncGenerator[Any, None]:
        """获取订阅生成器。"""
        q = asyncio.Queue()
        
        async def gen():
            # 发送历史数据
            async with self._history_lock:
                for item in self._history:
                    await q.put(item)
                
                # 如果已经结束，直接发停止信号
                if self._stopped:
                    await q.put(self._sentinel)
                else:
                    self._queues.append(q)

            try:
                while True:
                    value = await q.get()
                    if value is self._sentinel:
                        break
                    yield value
                    q.task_done()
            finally:
                # 清理
                if q in self._queues:
                    self._queues.remove(q)
        
        return gen()