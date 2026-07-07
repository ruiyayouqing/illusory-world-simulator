"""
[v10.5] SiliconFlow 文本嵌入函数 — 调用 BAAI/bge-m3 等模型 API。

替代 ChromaDB 默认的 all-MiniLM-L6-v2（英文小模型，中文效果差），
使用 SiliconFlow API 调用 bge-m3（1024维，中英文双语，质量高）。

支持：
- 批量嵌入（自动分批，每批最多 64 条）
- 本地 LRU 缓存（避免重复调用）
- 失败重试 + 回退到本地哈希（保证不崩溃）
"""
from __future__ import annotations

import hashlib
import logging
import time
from collections import OrderedDict
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from chromadb.api.types import Documents, Embeddings

logger = logging.getLogger("chronoverse.embedding")

# 本地缓存大小（LRU）
_CACHE_MAX = 2000


class SiliconFlowEmbeddingFunction:
    """调用 SiliconFlow /v1/embeddings API 的 ChromaDB 嵌入函数。

    兼容 ChromaDB 的 EmbeddingFunction 协议（实现 __call__ + name 属性）。
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.siliconflow.cn/v1",
        model_name: str = "BAAI/bge-m3",
        batch_size: int = 32,
        timeout: float = 30.0,
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model_name
        self._batch_size = min(batch_size, 64)  # SiliconFlow 单批上限 64
        self._timeout = timeout
        # LRU 缓存：text_hash -> embedding list
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._enabled = bool(api_key)
        # [P3-7] 可观测性统计计数器
        self._api_call_count: int = 0
        self._cache_hit_count: int = 0
        self._fallback_count: int = 0
        if not self._enabled:
            logger.warning(
                "SiliconFlowEmbeddingFunction: 未配置 api_key，将回退到本地哈希嵌入（仅供测试）"
            )

    def name(self) -> str:
        """ChromaDB 0.5+ 通过 name() 方法获取嵌入函数名称。"""
        return "siliconflow_embedding"

    def _cache_get(self, key: str) -> list[float] | None:
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)
        return self._cache[key]

    def _cache_put(self, key: str, value: list[float]) -> None:
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > _CACHE_MAX:
            self._cache.popitem(last=False)

    def _call_api(self, texts: list[str]) -> list[list[float]]:
        """调用 SiliconFlow embeddings API，带重试。"""
        # [Bug] 兼容 base_url 是否已含 /embeddings 后缀，避免拼出 /embeddings/embeddings
        base = self._base_url.rstrip("/")
        if base.endswith("/embeddings"):
            url = base
        else:
            url = f"{base}/embeddings"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "input": texts,
            "encoding_format": "float",
        }
        last_err = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    # SiliconFlow 返回 {"data": [{"embedding": [...], "index": 0}, ...]}
                    results = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
                    return [item["embedding"] for item in results]
            except Exception as e:
                last_err = e
                logger.warning(
                    "Embedding API attempt %d failed: %s", attempt + 1, e
                )
                if attempt < 2:
                    time.sleep(1.0 * (attempt + 1))
        raise RuntimeError(f"Embedding API 失败（3次重试后）: {last_err}")

    def _fallback_hash_embed(self, text: str, dim: int = 1024) -> list[float]:
        """本地哈希回退：当 API 不可用时生成确定性伪嵌入（仅供不崩溃，质量差）。"""
        h = hashlib.sha512(text.encode("utf-8")).digest()
        # 扩展到目标维度
        result = []
        for i in range(dim):
            byte_val = h[i % 64]
            # 加入文本长度和位置以增加变化
            result.append((byte_val / 255.0) * 2 - 1)
        return result

    def __call__(self, input: "Documents") -> "Embeddings":
        """ChromaDB 调用入口：接收文档列表，返回嵌入列表。"""
        if not isinstance(input, list):
            input = [input]

        # 空输入直接返回
        if not input:
            return []

        results: list[list[float] | None] = [None] * len(input)
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        # 查缓存
        for i, text in enumerate(input):
            cache_key = hashlib.md5(text.encode("utf-8")).hexdigest()
            cached = self._cache_get(cache_key)
            if cached is not None:
                results[i] = cached
                self._cache_hit_count += 1  # [P3-7] 缓存命中计数
            else:
                uncached_indices.append(i)
                uncached_texts.append(text)

        # 批量调用 API 处理未缓存项
        if uncached_texts:
            if self._enabled:
                try:
                    for start in range(0, len(uncached_texts), self._batch_size):
                        batch = uncached_texts[start : start + self._batch_size]
                        batch_embeddings = self._call_api(batch)
                        self._api_call_count += 1  # [P3-7] API 调用计数
                        for j, emb in enumerate(batch_embeddings):
                            idx = uncached_indices[start + j]
                            results[idx] = emb
                            cache_key = hashlib.md5(
                                uncached_texts[start + j].encode("utf-8")
                            ).hexdigest()
                            self._cache_put(cache_key, emb)
                except Exception as e:
                    logger.error(
                        "Embedding API 批量调用失败，回退到本地哈希: %s", e
                    )
                    # 回退到哈希
                    for j, text in enumerate(uncached_texts):
                        idx = uncached_indices[j]
                        if results[idx] is None:
                            results[idx] = self._fallback_hash_embed(text)
                            self._fallback_count += 1  # [P3-7] 回退计数
            else:
                # 未配置 key，直接用哈希回退
                for j, text in enumerate(uncached_texts):
                    idx = uncached_indices[j]
                    results[idx] = self._fallback_hash_embed(text)
                    self._fallback_count += 1  # [P3-7] 回退计数

        # 确保所有结果都已填充
        return [r if r is not None else self._fallback_hash_embed("") for r in results]

    def embed_query(self, input: "Documents") -> "Embeddings":
        """查询嵌入（与 __call__ 一致，bge-m3 不区分 query/document）。"""
        return self.__call__(input)

    # ChromaDB 0.5+ 需要这两个方法用于序列化
    @staticmethod
    def build_from_config(config: dict) -> "SiliconFlowEmbeddingFunction":
        return SiliconFlowEmbeddingFunction(**config)

    def get_config(self) -> dict:
        return {
            "api_key": self._api_key,
            "base_url": self._base_url,
            "model_name": self._model,
        }
