"""[v1.7 P2-3] 通用函数结果缓存（TTL + LRU）。

为非 LLM 的重复计算场景提供通用缓存：
- 向量检索结果缓存
- embedding 计算缓存
- JSON 解析缓存
- 启动期静态资源缓存

与 ``modules/llm_cache.py`` 的差异：
- ``llm_cache``：LLM 专用，含语义模糊匹配（3-gram Jaccard）
- 本模块：通用，纯 key 精确匹配，线程安全

使用示例::

    from modules.core.cache import cached, FunctionCache

    @cached(ttl=60.0)
    def search_memory(query: str, top_k: int = 5):
        return memory_store.search(query, top_k)

    # 手动控制
    cache = FunctionCache(ttl=60.0, max_size=200)
    key = cache.make_key("arg1", kwarg="val")
    if key in cache:
        result = cache[key]
    else:
        result = compute(...)
        cache[key] = result
"""
from __future__ import annotations

import functools
import hashlib
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, TypeVar

logger = logging.getLogger("chronoverse.core.cache")

T = TypeVar("T")


class FunctionCache:
    """线程安全的 TTL + LRU 函数结果缓存。

    参数：
        ttl: 缓存存活时间（秒），0 表示不过期
        max_size: 最大条目数，LRU 淘汰
    """

    def __init__(self, ttl: float = 60.0, max_size: int = 500):
        self.ttl = max(0.0, ttl)
        self.max_size = max(1, max_size)
        self._store: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    @staticmethod
    def make_key(*args, **kwargs) -> str:
        """根据参数生成缓存 key。

        注意：参数必须可序列化（str/int/float/tuple/None 或 JSON 可序列化对象）。
        """
        import json as _json
        try:
            payload = _json.dumps({"a": args, "k": kwargs}, sort_keys=True, default=str)
        except (TypeError, ValueError):
            payload = str((args, kwargs))
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

    def get(self, key: str) -> "Any | None":
        """获取缓存值。不存在或已过期返回 None（并记录 miss）。"""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            ts, value = entry
            if self.ttl > 0 and time.time() - ts > self.ttl:
                self._store.pop(key, None)
                self._misses += 1
                return None
            self._store.move_to_end(key)
            self._hits += 1
            return value

    def set(self, key: str, value: Any) -> None:
        """写入缓存。"""
        with self._lock:
            self._store[key] = (time.time(), value)
            self._store.move_to_end(key)
            while len(self._store) > self.max_size:
                self._store.popitem(last=False)
                self._evictions += 1

    def __contains__(self, key: str) -> bool:
        entry = self._store.get(key)
        if entry is None:
            return False
        ts, _ = entry
        if self.ttl > 0 and time.time() - ts > self.ttl:
            return False
        return True

    def __getitem__(self, key: str) -> Any:
        v = self.get(key)
        if v is None and key not in self._store:
            raise KeyError(key)
        return v

    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._hits = 0
            self._misses = 0
            self._evictions = 0

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._store),
                "max_size": self.max_size,
                "ttl": self.ttl,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "hit_rate": round(self._hits / total, 3) if total else 0.0,
            }


def cached(ttl: float = 60.0, max_size: int = 500, key_prefix: str = "") -> Callable:
    """装饰器：缓存函数返回值。

    参数：
        ttl: 缓存存活时间（秒）
        max_size: 最大条目数
        key_prefix: 缓存 key 前缀（避免不同函数 key 冲突）

    注意：
        - 返回值必须可被 Python 引用持有（不可序列化的对象也能缓存）
        - 函数参数必须是可哈希的（str/int/float/tuple/None/可 JSON 序列化对象）
        - 不要缓存有副作用的函数
    """
    cache = FunctionCache(ttl=ttl, max_size=max_size)
    prefix = key_prefix

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        nonlocal prefix
        if not prefix:
            prefix = f"{func.__module__}.{func.__qualname__}"

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = prefix + ":" + FunctionCache.make_key(*args, **kwargs)
            v = cache.get(key)
            if v is not None:
                return v
            v = func(*args, **kwargs)
            cache.set(key, v)
            return v

        wrapper.cache = cache  # type: ignore[attr-defined]
        wrapper.cache_clear = cache.clear  # type: ignore[attr-defined]
        wrapper.cache_stats = cache.stats  # type: ignore[attr-defined]
        return wrapper
    return decorator
