from __future__ import annotations
import asyncio
import logging
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger("chronoverse.tasks")


@dataclass
class QueuedTask:
    func: Callable[..., Any]
    args: tuple
    kwargs: dict
    is_async: bool = False
    posted_at: float = field(default_factory=time.time)


class BackgroundTaskQueue:
    """
    后台任务队列
    - 玩家响应路径上的非关键任务（存记忆、GraphRAG、审计等）丢到这里异步执行
    - 不阻塞玩家请求
    """

    def __init__(self, max_size: int = 200):
        self._queue: deque[QueuedTask] = deque(maxlen=max_size)
        self._worker_task: Optional[asyncio.Task] = None
        self._stats = {"submitted": 0, "completed": 0, "failed": 0}
        self._running = False

    def start(self):
        if self._worker_task is None or self._worker_task.done():
            # [Bug M9] 使用 get_running_loop 替代已弃用的 get_event_loop
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            self._worker_task = loop.create_task(self._worker_loop())
            self._running = True
            logger.info("Background task queue started")

    def stop(self):
        self._running = False
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            logger.info("Background task queue stopped")

    def post(self, func: Callable[..., Any], *args, **kwargs):
        """投递一个同步或异步任务到后台"""
        is_async = asyncio.iscoroutinefunction(func)
        task = QueuedTask(
            func=func,
            args=args,
            kwargs=kwargs,
            is_async=is_async
        )
        self._queue.append(task)
        self._stats["submitted"] += 1
        logger.debug("Task queued: %s, queue_size=%d", func.__name__, len(self._queue))
        if not self._running or (self._worker_task and self._worker_task.done()):
            try:
                self.start()
            except RuntimeError:
                pass

    async def _worker_loop(self):
        logger.info("Task worker loop starting")
        while self._running:
            try:
                if not self._queue:
                    await asyncio.sleep(0.1)
                    continue

                task = self._queue.popleft()
                try:
                    if task.is_async:
                        await task.func(*task.args, **task.kwargs)
                    else:
                        await asyncio.to_thread(task.func, *task.args, **task.kwargs)
                    self._stats["completed"] += 1
                except Exception as e:
                    self._stats["failed"] += 1
                    logger.warning("Background task %s failed: %s\n%s",
                                   task.func.__name__, e, traceback.format_exc())
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Worker loop error: %s", e, exc_info=True)
                await asyncio.sleep(1.0)
        logger.info("Task worker loop exiting")

    @property
    def pending_count(self) -> int:
        return len(self._queue)

    def get_stats(self) -> dict:
        return {
            **self._stats,
            "pending": len(self._queue),
            "running": self._running,
        }


class NullTaskQueue(BackgroundTaskQueue):
    """空队列实现，直接同步执行（用于测试或禁用后台队列时）"""

    def post(self, func: Callable[..., Any], *args, **kwargs):
        try:
            if asyncio.iscoroutinefunction(func):
                # [Bug M9] 使用 get_running_loop 替代已弃用的 get_event_loop
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                if loop.is_running():
                    loop.create_task(func(*args, **kwargs))
                    return
            func(*args, **kwargs)
            self._stats["completed"] += 1
        except Exception as e:
            self._stats["failed"] += 1
            logger.debug("NullQueue task %s failed (ignored): %s", func.__name__, e)
