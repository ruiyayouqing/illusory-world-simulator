"""
[v1.6] Embedding 相似度工具 — 用向量余弦相似度替换 Jaccard 匹配

包装 SiliconFlowEmbeddingFunction，提供：
  - similarity(a, b) -> float：余弦相似度 [0, 1]，失败返回 -1（调用方应回退）
  - best_match(query, candidates) -> (index, score)：批量匹配最佳候选

底层 SiliconFlowEmbeddingFunction 已内置 LRU 缓存，此处不重复缓存。
"""
from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .embedding_function import SiliconFlowEmbeddingFunction

logger = logging.getLogger("chronoverse.embedding_sim")


class EmbeddingSimilarity:
    """基于 Embedding 的文本相似度计算器。"""

    def __init__(self, embedding_function: "SiliconFlowEmbeddingFunction | None" = None):
        self._ef = embedding_function
        # 短文本（< 4 字符）直接走 Jaccard，省 API 调用
        self._min_len = 4
        # 一次失败后短期内不再重试（避免批量调用时刷屏报错）
        self._consecutive_failures = 0
        self._max_consecutive_failures = 5
        self._circuit_open = False

    def is_available(self) -> bool:
        """Embedding 相似度是否可用。"""
        return self._ef is not None and not self._circuit_open

    def similarity(self, a: str, b: str) -> float:
        """
        计算两段文本的余弦相似度。

        Returns:
            [0, 1] 范围的相似度；若 Embedding 不可用或失败，返回 -1.0（调用方应回退）。
        """
        if not self._ef or self._circuit_open:
            return -1.0
        if not a or not b:
            return -1.0
        # 短文本：embedding 区分度低且 API 成本不划算，直接返回 -1 让调用方走 Jaccard
        if len(a) < self._min_len or len(b) < self._min_len:
            return -1.0

        try:
            vecs = self._ef.embed_query([a, b])
            if not vecs or len(vecs) < 2:
                self._record_failure()
                return -1.0
            va, vb = vecs[0], vecs[1]
            if va is None or vb is None:
                self._record_failure()
                return -1.0
            return _cosine(va, vb)
        except Exception as e:
            logger.debug("[EmbeddingSim] similarity failed: %s", e)
            self._record_failure()
            return -1.0

    def best_match(
        self, query: str, candidates: list[str]
    ) -> tuple[int, float]:
        """
        在候选列表中找到与 query 最相似的文本。

        Returns:
            (best_index, best_score)。若无可用候选或失败返回 (-1, -1.0)。
        """
        if not self._ef or self._circuit_open or not query or not candidates:
            return -1, -1.0
        # 过滤掉过短的候选
        valid = [(i, c) for i, c in enumerate(candidates) if c and len(c) >= self._min_len]
        if not valid:
            return -1, -1.0

        try:
            texts = [query] + [c for _, c in valid]
            vecs = self._ef.embed_query(texts)
            if not vecs or len(vecs) < len(texts):
                self._record_failure()
                return -1, -1.0
            q_vec = vecs[0]
            if q_vec is None:
                self._record_failure()
                return -1, -1.0

            best_idx = -1
            best_score = -1.0
            for offset, (orig_idx, _) in enumerate(valid):
                v = vecs[offset + 1]
                if v is None:
                    continue
                score = _cosine(q_vec, v)
                if score > best_score:
                    best_score = score
                    best_idx = orig_idx
            return best_idx, best_score
        except Exception as e:
            logger.debug("[EmbeddingSim] best_match failed: %s", e)
            self._record_failure()
            return -1, -1.0

    def _record_failure(self):
        """记录连续失败，超过阈值后熔断。"""
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._max_consecutive_failures:
            self._circuit_open = True
            logger.warning(
                "[EmbeddingSim] Embedding API 连续失败 %d 次，已熔断（回退到 Jaccard）",
                self._consecutive_failures,
            )

    def reset_circuit(self):
        """重置熔断状态（配置变更或手动恢复时调用）。"""
        self._consecutive_failures = 0
        self._circuit_open = False


def _cosine(a: list[float], b: list[float]) -> float:
    """计算余弦相似度，结果裁剪到 [0, 1]。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for i in range(len(a)):
        av = a[i]
        bv = b[i]
        dot += av * bv
        na += av * av
        nb += bv * bv
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    sim = dot / math.sqrt(na * nb)
    # 裁剪到 [0, 1]（bge-m3 输出已经归一化，但保险起见）
    return max(0.0, min(1.0, sim))
