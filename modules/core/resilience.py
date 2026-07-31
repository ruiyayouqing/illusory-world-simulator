"""[v1.7 P2-2] 韧性工具：重试 / 熔断 / 超时 / 安全调用。

提供四个独立可组合的原语，不依赖任何外部库，可被任意模块复用：

1. ``retry_with_backoff`` — 装饰器：指数退避重试
2. ``CircuitBreaker`` — 熔断器：连续失败后短路，定时半开探测
3. ``call_with_timeout`` — 线程级超时：防止调用永久挂起
4. ``safe_call`` — 安全调用：异常时返回 fallback 值

使用示例::

    from modules.core.resilience import retry_with_backoff, CircuitBreaker, safe_call

    @retry_with_backoff(max_retries=3, base_delay=0.5)
    def fetch_data(url):
        ...

    breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)
    if breaker.allow():
        try:
            result = breaker.call(dangerous_func, arg1)
        except CircuitOpenError:
            result = fallback()
    else:
        result = fallback()

    # 或简单包装
    text = safe_call(llm.chat, prompt, fallback="（生成失败）")
"""
from __future__ import annotations

import functools
import logging
import random
import threading
import time
from typing import Any, Callable, TypeVar

logger = logging.getLogger("chronoverse.core.resilience")

T = TypeVar("T")


# ──────────────────────────────────────────────────────────────
# 1. 重试 + 指数退避
# ──────────────────────────────────────────────────────────────


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    exceptions: tuple = (Exception,),
    on_retry: Callable[[Exception, int], None] | None = None,
):
    """装饰器：对同步函数做指数退避重试。

    参数：
        max_retries: 最大重试次数（不含首次调用）
        base_delay: 首次重试延迟（秒）
        max_delay: 单次重试延迟上限（秒）
        exceptions: 触发重试的异常类型
        on_retry: 重试回调 ``callback(exc, attempt)``，默认记日志

    返回值与原函数一致；全部失败后抛出最后一次异常。

    注意：异步函数请用 ``aretry_with_backoff``。
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exc: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt >= max_retries:
                        break
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    delay += random.uniform(0, delay * 0.1)  # jitter
                    if on_retry:
                        on_retry(e, attempt + 1)
                    else:
                        logger.warning(
                            "retry %s attempt %d/%d in %.2fs: %s",
                            func.__name__, attempt + 1, max_retries, delay, e,
                        )
                    time.sleep(delay)
            assert last_exc is not None
            raise last_exc
        return wrapper
    return decorator


def aretry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    exceptions: tuple = (Exception,),
):
    """异步版本的重试装饰器。"""
    import asyncio

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exc: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt >= max_retries:
                        break
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    delay += random.uniform(0, delay * 0.1)
                    logger.warning(
                        "aretry %s attempt %d/%d in %.2fs: %s",
                        func.__name__, attempt + 1, max_retries, delay, e,
                    )
                    await asyncio.sleep(delay)
            assert last_exc is not None
            raise last_exc
        return wrapper
    return decorator


# ──────────────────────────────────────────────────────────────
# 2. 熔断器
# ──────────────────────────────────────────────────────────────


class CircuitOpenError(Exception):
    """熔断器开启时抛出，调用方应走 fallback。"""


class CircuitBreaker:
    """线程安全的熔断器。

    状态机：
        CLOSED → 连续失败达 threshold → OPEN
        OPEN   → 经过 recovery_timeout → HALF_OPEN
        HALF_OPEN → 下次调用成功 → CLOSED
                  → 下次调用失败 → OPEN

    用法::

        breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)
        if not breaker.allow():
            return fallback()
        try:
            result = breaker.call(func, *args)
        except CircuitOpenError:
            return fallback()
    """

    _CLOSED = "closed"
    _OPEN = "open"
    _HALF_OPEN = "half_open"

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        success_threshold: int = 1,
    ):
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_timeout = max(0.1, recovery_timeout)
        self.success_threshold = max(1, success_threshold)
        self._state = self._CLOSED
        self._failures = 0
        self._successes = 0
        self._last_failure_time = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._state

    def allow(self) -> bool:
        """是否允许调用。OPEN 状态返回 False。"""
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._state != self._OPEN

    def record_success(self) -> None:
        with self._lock:
            if self._state == self._HALF_OPEN:
                self._successes += 1
                if self._successes >= self.success_threshold:
                    self._reset_to_closed()
            elif self._state == self._CLOSED:
                self._failures = 0

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._last_failure_time = time.time()
            if self._state == self._HALF_OPEN:
                self._state = self._OPEN
                self._successes = 0
            elif self._state == self._CLOSED:
                if self._failures >= self.failure_threshold:
                    self._state = self._OPEN
                    logger.warning(
                        "CircuitBreaker OPEN after %d failures", self._failures,
                    )

    def call(self, func: Callable[..., T], *args, **kwargs) -> T:
        """通过熔断器调用函数。OPEN 时抛 CircuitOpenError。"""
        if not self.allow():
            raise CircuitOpenError("circuit breaker is open")
        try:
            result = func(*args, **kwargs)
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result

    def reset(self) -> None:
        """手动重置到 CLOSED 状态。"""
        with self._lock:
            self._reset_to_closed()

    def stats(self) -> dict:
        with self._lock:
            self._maybe_transition_to_half_open()
            return {
                "state": self._state,
                "failures": self._failures,
                "successes": self._successes,
                "failure_threshold": self.failure_threshold,
                "recovery_timeout": self.recovery_timeout,
            }

    def _maybe_transition_to_half_open(self) -> None:
        if self._state == self._OPEN:
            if time.time() - self._last_failure_time >= self.recovery_timeout:
                self._state = self._HALF_OPEN
                self._successes = 0
                logger.info("CircuitBreaker → HALF_OPEN (probing)")

    def _reset_to_closed(self) -> None:
        self._state = self._CLOSED
        self._failures = 0
        self._successes = 0


# ──────────────────────────────────────────────────────────────
# 3. 线程级超时
# ──────────────────────────────────────────────────────────────


class TimeoutError(Exception):
    """call_with_timeout 超时。"""


def call_with_timeout(
    func: Callable[..., T],
    timeout_sec: float,
    *args,
    fallback: T | None = None,
    use_fallback: bool = False,
    **kwargs,
) -> T:
    """在子线程中调用 ``func``，超时返回 fallback 或抛 TimeoutError。

    参数：
        func: 要调用的函数
        timeout_sec: 超时秒数
        fallback: 超时时的返回值（仅当 use_fallback=True 时生效）
        use_fallback: True 时超时返回 fallback，False 时超时抛 TimeoutError

    注意：基于 ``threading`` 实现，无法强制终止子线程中的阻塞调用
    （如 socket recv）。超时后子线程仍在后台运行直到函数返回，但主线程
    不再等待。适用于 LLM 调用等有自身超时的场景。
    """
    result_box: list[Any] = [None]
    exc_box: list[BaseException | None] = [None]
    done = threading.Event()

    def _worker():
        try:
            result_box[0] = func(*args, **kwargs)
        except BaseException as e:
            exc_box[0] = e
        finally:
            done.set()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    completed = done.wait(timeout=timeout_sec)

    if not completed:
        if use_fallback:
            logger.warning("call_with_timeout timed out (%.1fs): %s", timeout_sec, func.__name__)
            return fallback  # type: ignore
        raise TimeoutError(f"{func.__name__} timed out after {timeout_sec}s")

    if exc_box[0] is not None:
        raise exc_box[0]
    return result_box[0]  # type: ignore


# ──────────────────────────────────────────────────────────────
# 4. 安全调用
# ──────────────────────────────────────────────────────────────


def safe_call(
    func: Callable[..., T],
    *args,
    fallback: T,
    log_error: bool = True,
    **kwargs,
) -> T:
    """调用 ``func``，任何异常时返回 ``fallback``。

    适用于非关键路径：失败不影响主流程，只需记日志并降级。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        if log_error:
            logger.warning("safe_call %s failed: %s", getattr(func, "__name__", "?"), e)
        return fallback
