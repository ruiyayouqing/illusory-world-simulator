from __future__ import annotations
import hashlib
import json
import time
import logging
from .llm.base_llm import BaseLLM

logger = logging.getLogger("chronoverse.llm_cache")


class LLMCache:
    def __init__(self, llm: BaseLLM, max_size: int = 500, ttl: int = 300):
        self.llm = llm
        self.cache: dict[str, dict] = {}
        self.max_size = max_size
        # [B] TTL 机制：与 MimoLLM._cache 统一为 300s，避免长期缓存过时数据
        # 原先只有 LRU 淘汰，无 TTL，导致同一 prompt 永远返回旧响应
        self.ttl: int = int(ttl)
        self.hit_count: int = 0
        self.miss_count: int = 0
        self.semantic_hit_count: int = 0
        self.expired_count: int = 0  # [B] TTL 过期计数

    def _make_key(self, prompt: str, temperature: float) -> str:
        content = f"{prompt}_{temperature}"
        return hashlib.md5(content.encode()).hexdigest()

    def _text_similarity(self, a: str, b: str) -> float:
        """[v9] 字符级 Jaccard 相似度，无需外部依赖"""
        if not a or not b:
            return 0.0
        # 用 3-gram 提取特征
        def ngrams(text, n=3):
            text = text[:300]  # 限制长度避免性能问题
            return set(text[i:i+n] for i in range(len(text) - n + 1))
        set_a = ngrams(a)
        set_b = ngrams(b)
        if not set_a or not set_b:
            return 0.0
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union if union > 0 else 0.0

    def _find_semantic_match(self, prompt: str, temperature: float,
                             threshold: float = 0.85) -> str | None:
        """[v9] 语义模糊匹配：在缓存中查找相似 prompt"""
        if not self.cache:
            return None
        for key, entry in self.cache.items():
            # [B] TTL 检查：跳过过期条目
            if self._is_expired(entry):
                continue
            # 温度必须相近（±0.2）
            if abs(entry.get("temperature", 0) - temperature) > 0.2:
                continue
            similarity = self._text_similarity(prompt, entry.get("prompt", ""))
            if similarity >= threshold:
                return key
        return None

    def _is_expired(self, entry: dict) -> bool:
        """[B] 检查缓存条目是否过期（TTL）"""
        if self.ttl <= 0:
            return False  # TTL=0 表示永不过期
        age = time.time() - entry.get("timestamp", 0)
        return age > self.ttl

    def _get_valid_entry(self, key: str) -> dict | None:
        """[B] 获取未过期的缓存条目，过期则删除并返回 None"""
        entry = self.cache.get(key)
        if entry is None:
            return None
        if self._is_expired(entry):
            del self.cache[key]
            self.expired_count += 1
            return None
        return entry

    def chat(self, prompt: str, temperature: float = 0.8,
             max_tokens: int = 4096) -> str:
        key = self._make_key(prompt, temperature)
        # 精确匹配（含 TTL 检查）
        entry = self._get_valid_entry(key)
        if entry:
            self.hit_count += 1
            return entry["response"]
        # [v9] 语义模糊匹配
        semantic_key = self._find_semantic_match(prompt, temperature)
        if semantic_key:
            self.semantic_hit_count += 1
            logger.debug("Semantic cache hit for prompt similarity")
            return self.cache[semantic_key]["response"]
        self.miss_count += 1
        response = self.llm.chat(prompt, temperature=temperature, max_tokens=max_tokens)
        self._store(key, prompt, response, temperature)
        return response

    def chat_json(self, prompt: str, temperature: float = 0.5,
                  max_tokens: int = 4096) -> dict:
        key = self._make_key(prompt, temperature)
        # 精确匹配（含 TTL 检查）
        entry = self._get_valid_entry(key)
        if entry:
            self.hit_count += 1
            return json.loads(entry["response"])
        # [v9] 语义模糊匹配
        semantic_key = self._find_semantic_match(prompt, temperature)
        if semantic_key:
            self.semantic_hit_count += 1
            logger.debug("Semantic cache hit for JSON prompt")
            return json.loads(self.cache[semantic_key]["response"])
        self.miss_count += 1
        response = self.llm.chat_json(prompt, temperature=temperature, max_tokens=max_tokens)
        self._store(key, prompt, json.dumps(response, ensure_ascii=False), temperature)
        return response

    def _store(self, key: str, prompt: str, response: str, temperature: float):
        # 通过 LRU 淘汰条目数限制内存，而非截断内容（截断会导致缓存命中返回残缺数据）
        if len(self.cache) >= self.max_size:
            oldest = min(self.cache.keys(), key=lambda k: self.cache[k]["timestamp"])
            del self.cache[oldest]
        self.cache[key] = {
            "prompt": prompt,
            "response": response,
            "temperature": temperature,
            "timestamp": time.time(),
        }

    def get_stats(self) -> dict:
        total = self.hit_count + self.miss_count + self.semantic_hit_count
        # [v1.7 P3-A] 数值型命中率（便于聚合报告与阈值告警）
        hit_rate_float = round(
            (self.hit_count + self.semantic_hit_count) / total * 100, 2
        ) if total > 0 else 0.0
        return {
            "hits": self.hit_count,
            "semantic_hits": self.semantic_hit_count,
            "misses": self.miss_count,
            "hit_rate": f"{hit_rate_float:.1f}%",
            "hit_rate_float": hit_rate_float,  # [v1.7 P3-A] 数值型
            "total_requests": total,  # [v1.7 P3-A] 便于聚合
            "cache_size": len(self.cache),
            "expired": self.expired_count,  # [B] TTL 过期计数
            "ttl_seconds": self.ttl,
        }

    def clear(self):
        self.cache.clear()
        self.hit_count = 0
        self.miss_count = 0
        self.semantic_hit_count = 0
        self.expired_count = 0
