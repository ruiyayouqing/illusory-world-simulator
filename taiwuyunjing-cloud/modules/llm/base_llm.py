from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

@dataclass
class LLMUsageStats:
    """LLM调用统计信息"""
    total_calls: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cache_hit_tokens: int = 0
    total_cache_miss_tokens: int = 0
    failed_calls: int = 0
    total_latency_ms: float = 0.0

    def record_call(self, prompt_tokens: int = 0, completion_tokens: int = 0,
                    cache_hit_tokens: int = 0, latency_ms: float = 0.0,
                    failed: bool = False):
        self.total_calls += 1
        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens
        self.total_cache_hit_tokens += cache_hit_tokens
        self.total_cache_miss_tokens += max(0, prompt_tokens - cache_hit_tokens)
        self.total_latency_ms += latency_ms
        if failed:
            self.failed_calls += 1

    @property
    def avg_latency_ms(self) -> float:
        if self.total_calls == 0:
            return 0.0
        return self.total_latency_ms / self.total_calls

    @property
    def cache_hit_rate(self) -> float:
        total = self.total_cache_hit_tokens + self.total_cache_miss_tokens
        if total == 0:
            return 0.0
        return self.total_cache_hit_tokens / total

    def to_dict(self) -> dict:
        return {
            "total_calls": self.total_calls,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_cache_hit_tokens": self.total_cache_hit_tokens,
            "cache_hit_rate": round(self.cache_hit_rate, 3),
            "failed_calls": self.failed_calls,
            "avg_latency_ms": round(self.avg_latency_ms, 1),
        }


@dataclass
class LastUsage:
    """最近一次调用的usage信息"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hit_tokens: int = 0
    latency_ms: float = 0.0
    model: str = ""


class BaseLLM(ABC):
    def __init__(self):
        self.stats = LLMUsageStats()
        self.last_usage = LastUsage()
        self.model_name: str = "unknown"

    @abstractmethod
    def chat(self, prompt: str, temperature: float = 0.8, max_tokens: int = 4096) -> str:
        ...

    @abstractmethod
    def chat_json(self, prompt: str, temperature: float = 0.5, max_tokens: int = 4096) -> dict:
        ...

    @abstractmethod
    async def achat(self, prompt: str, temperature: float = 0.8, max_tokens: int = 4096) -> str:
        ...

    @abstractmethod
    async def achat_json(self, prompt: str, temperature: float = 0.5, max_tokens: int = 4096) -> dict:
        ...

    def chat_json_from_messages(self, messages: list[dict],
                                temperature: float = 0.5,
                                max_tokens: int = 4096) -> dict:
        """从消息列表生成 JSON 响应（子类可覆盖以原生支持多轮消息）"""
        # [Bug#14] 回退到 chat_json 而非抛 NotImplementedError，
        # 避免 Router 的 hasattr 检查通过但调用时崩溃
        prompt = "\n".join(f"[{m.get('role','user')}]: {m.get('content','')}" for m in messages)
        return self.chat_json(prompt, temperature=temperature, max_tokens=max_tokens)

    def chat_structured(self, prompt: str, schema_name: str,
                        temperature: float = 0.7, max_tokens: int = 2048) -> dict:
        """
        结构化输出：使用 JSON Schema 约束 LLM 输出。

        子类可覆盖以原生支持 response_format 参数。
        默认实现回退到 chat_json，保持向后兼容。
        """
        return self.chat_json(prompt, temperature=temperature, max_tokens=max_tokens)

    def chat_stream(self, prompt: str, temperature: float = 0.8,
                    max_tokens: int = 4096):
        """流式生成文本，返回生成器逐 token yield（子类可覆盖）"""
        # [Bug#14] 回退到 chat 而非抛 NotImplementedError，
        # 避免 Router/TaskBoundLLM 的 hasattr 检查通过但调用时崩溃
        result = self.chat(prompt, temperature=temperature, max_tokens=max_tokens)
        yield result

    def get_stats(self) -> dict:
        return self.stats.to_dict()

    def reset_stats(self):
        self.stats = LLMUsageStats()

    def close(self):
        """[Bug] 关闭底层连接池，子类应覆盖以释放 httpx 客户端"""
        pass
