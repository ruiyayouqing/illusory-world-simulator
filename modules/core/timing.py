"""[v1.7 P2-3] 延迟分析工具：耗时打点 + 性能统计。

提供轻量级耗时打点机制，不依赖任何外部库，可被任意模块复用：

1. ``TimingCollector`` — 进程级单例，收集所有打点
2. ``timed`` — 装饰器：自动记录函数耗时
3. ``TimingScope`` — 上下文管理器：手动控制打点范围

使用示例::

    from modules.core.timing import timed, TimingCollector

    @timed(category="llm")
    def chat(prompt): ...

    with TimingCollector.scope("retrieval"):
        results = retriever.retrieve(query)

    # 获取统计
    stats = TimingCollector.stats()
    # {
    #   "total_calls": 100,
    #   "total_time_ms": 12345.6,
    #   "by_category": {
    #     "llm": {"calls": 50, "time_ms": 10000.0, "avg_ms": 200.0},
    #     "retrieval": {"calls": 50, "time_ms": 2345.6, "avg_ms": 46.9},
    #   },
    #   "slow_calls": [...],  # 慢调用详情（> threshold）
    # }
"""
from __future__ import annotations

import functools
import logging
import threading
import time
from contextlib import contextmanager
from typing import Callable, TypeVar

logger = logging.getLogger("chronoverse.core.timing")

T = TypeVar("T")


class TimingCollector:
    """进程级耗时收集器单例。

    线程安全。默认收集最近 1000 条调用记录，慢调用阈值 1.0s。
    """

    _instance: "TimingCollector | None" = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._records: list[dict] = []
                    inst._max_records = 1000
                    inst._slow_threshold_sec = 1.0
                    inst._max_slow_records = 50
                    inst._slow_records: list[dict] = []
                    inst._enabled = True
                    inst._record_lock = threading.Lock()
                    cls._instance = inst
        return cls._instance

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, v: bool):
        self._enabled = bool(v)

    @contextmanager
    def scope(self, category: str, label: str = ""):
        """上下文管理器：记录一段代码的耗时。

        参数：
            category: 分类（如 "llm" / "retrieval" / "db"）
            label: 可选标签（如 "chat_stream" / "vector_search"）
        """
        if not self._enabled:
            yield
            return
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self.record(category, label, elapsed)

    def record(self, category: str, label: str, elapsed_sec: float) -> None:
        """记录一次调用耗时。"""
        if not self._enabled:
            return
        entry = {
            "category": category,
            "label": label,
            "elapsed_ms": round(elapsed_sec * 1000, 2),
            "ts": time.time(),
        }
        with self._record_lock:
            self._records.append(entry)
            if len(self._records) > self._max_records:
                self._records = self._records[-self._max_records:]
            if elapsed_sec >= self._slow_threshold_sec:
                self._slow_records.append(entry)
                if len(self._slow_records) > self._max_slow_records:
                    self._slow_records = self._slow_records[-self._max_slow_records:]

    def stats(self) -> dict:
        """获取性能统计。"""
        with self._record_lock:
            by_category: dict[str, dict] = {}
            total_time_ms = 0.0
            total_calls = len(self._records)
            for r in self._records:
                cat = r["category"]
                if cat not in by_category:
                    by_category[cat] = {"calls": 0, "time_ms": 0.0}
                by_category[cat]["calls"] += 1
                by_category[cat]["time_ms"] += r["elapsed_ms"]
                total_time_ms += r["elapsed_ms"]
            for cat, s in by_category.items():
                s["avg_ms"] = round(s["time_ms"] / s["calls"], 2) if s["calls"] else 0.0
                s["time_ms"] = round(s["time_ms"], 2)
            return {
                "total_calls": total_calls,
                "total_time_ms": round(total_time_ms, 2),
                "by_category": by_category,
                "slow_calls": list(self._slow_records),
                "slow_threshold_ms": round(self._slow_threshold_sec * 1000, 2),
                "enabled": self._enabled,
            }

    def recent(self, limit: int = 50, category: str = "") -> list[dict]:
        """获取最近 N 条记录。"""
        with self._record_lock:
            items = list(self._records)
        items.reverse()
        if category:
            items = [r for r in items if r.get("category") == category]
        return items[:limit]

    def clear(self) -> None:
        """清空所有记录。"""
        with self._record_lock:
            self._records.clear()
            self._slow_records.clear()


# 模块级单例
TimingCollectorInstance = TimingCollector()


def timed(category: str, label: str = "") -> Callable:
    """装饰器：自动记录函数耗时。

    参数：
        category: 分类（如 "llm" / "retrieval" / "db"）
        label: 可选标签，默认用函数名
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        lbl = label or func.__name__

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if not TimingCollectorInstance.enabled:
                return func(*args, **kwargs)
            start = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                elapsed = time.perf_counter() - start
                TimingCollectorInstance.record(category, lbl, elapsed)

        return wrapper
    return decorator


def reset_timing() -> None:
    """重置单例（测试用）。"""
    TimingCollectorInstance.clear()
