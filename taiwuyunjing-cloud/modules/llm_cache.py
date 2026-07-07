from __future__ import annotations
import hashlib
import json
import time
import logging
from .llm.base_llm import BaseLLM

logger = logging.getLogger("chronoverse.llm_cache")


class LLMCache:
    def __init__(self, llm: BaseLLM, max_size: int = 500):
        self.llm = llm
        self.cache: dict[str, dict] = {}
        self.max_size = max_size
        self.hit_count: int = 0
        self.miss_count: int = 0
        self.semantic_hit_count: int = 0

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
            # 温度必须相近（±0.2）
            if abs(entry.get("temperature", 0) - temperature) > 0.2:
                continue
            similarity = self._text_similarity(prompt, entry.get("prompt", ""))
            if similarity >= threshold:
                return key
        return None

    def chat(self, prompt: str, temperature: float = 0.8,
             max_tokens: int = 4096) -> str:
        key = self._make_key(prompt, temperature)
        # 精确匹配
        if key in self.cache:
            self.hit_count += 1
            return self.cache[key]["response"]
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
        # 精确匹配
        if key in self.cache:
            self.hit_count += 1
            return json.loads(self.cache[key]["response"])
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
        return {
            "hits": self.hit_count,
            "semantic_hits": self.semantic_hit_count,
            "misses": self.miss_count,
            "hit_rate": f"{(self.hit_count + self.semantic_hit_count)/total*100:.1f}%" if total > 0 else "0%",
            "cache_size": len(self.cache),
        }

    def clear(self):
        self.cache.clear()
        self.hit_count = 0
        self.miss_count = 0
        self.semantic_hit_count = 0
