import asyncio

class AsyncGeneratorManager:
    def __init__(self, maxsize: int = 0):
        self._queue = asyncio.Queue(maxsize=maxsize)
        self._closed = False
        self._exhausted = False
        self._sentinel = object()

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.get()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # 如果是因为异常或 break 退出上下文，可能队列是满的且没人消费。
        # 此时强制关闭，不要无限等待。
        await self.aclose(force=True)

    async def get(self):
        if self._exhausted:
            raise StopAsyncIteration

        value = await self._queue.get()
        self._queue.task_done()

        if value is self._sentinel:
            self._exhausted = True
            raise StopAsyncIteration
        
        return value

    async def put(self, value):
        if self._closed:
            raise RuntimeError("Cannot put into a closed generator")
        
        # 直接 await，不要加锁。
        # 即使在 await 期间 closed 变成了 True，
        # 这个数据放入队列也是安全的（它会在哨兵之前被消费，或者在哨兵之后成为不可达数据）
        await self._queue.put(value)

        # 3. 再次检查 (可选，为了更严格的逻辑)
        # 如果在 await 期间 generator 被关闭了，这个值虽然进去了，
        # 但我们可以选择抛出异常通知生产者。
        # 不过通常只要数据进去了，不做处理也可以。
        if self._closed:
             # 这里只是为了通知生产者停止，实际上数据可能在哨兵之后
             raise RuntimeError("Generator closed during put")

    def put_nowait(self, value):
        # put_nowait 是同步方法，没法用 async lock，但在 asyncio 单线程模型下，
        # 只要不 await，中间就不会切换，相对安全。但为了逻辑严谨：
        if self._closed:
            raise RuntimeError("Cannot put into a closed generator")
        self._queue.put_nowait(value)

    def close(self):
        """同步关闭：尽力放入哨兵，如果满了则抛出异常提醒"""
        if self._closed:
            return
        
        # 这里不需要锁，因为只是设置标记和放哨兵
        # 如果 put 正在持有锁等待空位，这里 put_nowait 会失败，符合预期
        self._closed = True
        try:
            self._queue.put_nowait(self._sentinel)
        except asyncio.QueueFull:
            # 队列满时的同步策略：
            # 必须牺牲一个旧数据来终止迭代，否则消费者会卡死。
            try:
                _ = self._queue.get_nowait()
                self._queue.task_done()
                self._queue.put_nowait(self._sentinel)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                # 极端并发下的防御（例如刚好被别人抢了位置）
                pass

    async def aclose(self, force=False):
        """
        异步关闭。
        :param force: 
            True: 确保关闭。如果队列满，移除旧数据以插入哨兵。适用于 __aexit__ 清理。
            False: 优雅关闭。等待空位插入哨兵。注意：如果消费者已停止消费，这会导致死锁。
        """
        if self._closed:
            return
        self._closed = True

        # 在锁外执行 put，避免持有锁等待导致 put 无法进行
        if force:
            while True:
                try:
                    self._queue.put_nowait(self._sentinel)
                    break
                except asyncio.QueueFull:
                    # 队列满了，为了防止死锁（因为可能没人消费了），
                    # 我们只能丢弃一个旧数据来腾位置给哨兵。
                    try:
                        _ = self._queue.get_nowait()
                        self._queue.task_done()
                    except asyncio.QueueEmpty:
                        continue # 竞争条件下可能空了，继续重试
        else:
            # 正常关闭，等待空位。注意：如果消费者已死，这里会死锁。
            await self._queue.put(self._sentinel)

# --- 测试用例 ---
async def main():
    gen = BaseAsyncGenerator()

    # 模拟生产者
    async def producer():
        print("Producer: start")
        for i in range(5):
            await asyncio.sleep(0.1)
            await gen.put(i)
            print(f"Producer: put {i}")
        print("Producer: closing")
        gen.close()

    # 模拟消费者
    async def consumer():
        print("Consumer: start waiting")
        async for item in gen:
            print(f"Consumer: got {item}")
        print("Consumer: finished")

    await asyncio.gather(producer(), consumer())

if __name__ == "__main__":
    asyncio.run(main())