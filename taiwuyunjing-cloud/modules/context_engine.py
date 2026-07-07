"""上下文工程：注意力预算管理 + 提示压缩 + Prompt Caching。

本模块在现有 ``context_budget``（场景感知的按比例分配）之上，提供基于
**优先级** 的注意力预算分配、轻量提示压缩以及稳定前缀缓存复用。

与 ``context_budget.py`` 的协同关系：
- ``ContextEngine`` 复用 ``context_budget.estimate_tokens`` 进行 token 估算，
  保证两套预算体系口径一致（优先 tiktoken，回退到启发式）。
- ``context_budget.build_system_context_with_budget`` 仍作为回退路径保留，
  适用于未注入 ContextEngine 的旧调用方。
- 当 ContextEngine 可用时，优先使用按优先级裁剪 + 压缩的策略；失败时回退。
"""
from __future__ import annotations
import logging
import hashlib
from dataclasses import dataclass, field
from typing import Optional

from .context_budget import estimate_tokens as _estimate_tokens_shared

logger = logging.getLogger("chronoverse.context_engine")


@dataclass
class ContextLayer:
    """上下文层：每层有名称、内容、优先级、可压缩性。"""
    name: str
    content: str
    priority: int  # 1=最高（不可裁剪），10=最低（可裁剪）
    compressible: bool = True
    min_tokens: int = 0  # 压缩后最少保留的 token 数
    cached: bool = False  # 是否可缓存（稳定前缀）


@dataclass
class ContextBudget:
    """注意力预算。"""
    max_tokens: int
    reserved: int = 0
    layers: list[ContextLayer] = field(default_factory=list)

    @property
    def available(self) -> int:
        # [P0-2] 加 max(0, ...) 保护，避免预算为负导致后续逻辑异常
        return max(0, self.max_tokens - self.reserved)

    def add_layer(self, layer: ContextLayer):
        self.layers.append(layer)

    def allocate(self) -> dict[str, str]:
        """
        按优先级分配 token 预算。
        高优先级层先分配，低优先级层可能被压缩或裁剪。
        返回 {layer_name: final_content}

        [v11] P1b: Protected/Compressible 分层加强。
        - priority <= 3 且 compressible=False 的层为「绝对保护层」，永不压缩
        - 其余层按优先级分配，预算不足时跳过最低优先级的可压缩层
        """
        # 按优先级排序（数字小的优先）
        sorted_layers = sorted(self.layers, key=lambda l: l.priority)

        result: dict[str, str] = {}
        total_used = 0

        # 第一轮：分配绝对保护层（priority <= 3 且不可压缩）
        # 这些层永远不会被压缩或跳过：system, world, character, active_foreshadows
        for layer in sorted_layers:
            if not layer.compressible and layer.priority <= 3:
                tokens = self._estimate_tokens(layer.content)
                if total_used + tokens > self.available and self.available > 0:
                    # [v11] 绝对保护层：即使超预算也只截断，不删除内容
                    max_chars = int(len(layer.content) * (self.available - total_used) / max(1, tokens))
                    if max_chars > 100:
                        result[layer.name] = layer.content[:max_chars] + "\n...(预算限制截断)..."
                    else:
                        result[layer.name] = layer.content  # 极端情况也不丢
                    total_used = self.available
                    logger.warning(
                        "Protected layer '%s' (priority=%d): truncated to fit budget",
                        layer.name, layer.priority,
                    )
                else:
                    result[layer.name] = layer.content
                    total_used += tokens
                    logger.debug(
                        "Protected layer '%s' (priority=%d): %d tokens (fixed)",
                        layer.name, layer.priority, tokens,
                    )

        # 第二轮：分配可压缩层（按优先级，预算不足时跳过最低优先级的）
        remaining = self.available - total_used
        for layer in sorted_layers:
            if layer.name in result:
                continue
            tokens = self._estimate_tokens(layer.content)
            if tokens <= remaining * 0.3:  # 该层不超过剩余预算的30%
                result[layer.name] = layer.content
                remaining -= tokens
                logger.debug(
                    "Layer '%s' (priority=%d): %d tokens (full)",
                    layer.name, layer.priority, tokens,
                )
            elif remaining > layer.min_tokens:
                # 压缩该层
                compressed = self._compress(layer.content, remaining)
                result[layer.name] = compressed
                remaining -= self._estimate_tokens(compressed)
                logger.debug(
                    "Layer '%s' (priority=%d): compressed to %d tokens",
                    layer.name, layer.priority, self._estimate_tokens(compressed),
                )
            else:
                # 预算耗尽，跳过该层
                result[layer.name] = ""
                logger.debug(
                    "Layer '%s' (priority=%d): skipped (budget exhausted)",
                    layer.name, layer.priority,
                )

        return result

    def _estimate_tokens(self, text: str) -> int:
        """估算 token 数。复用 context_budget.estimate_tokens 保持口径一致。"""
        return _estimate_tokens_shared(text)

    def _compress(self, text: str, target_tokens: int) -> str:
        """轻量压缩：截断 + 保留首尾。尾部通常更重要（包含最近信息）。"""
        if not text:
            return ""
        current_tokens = self._estimate_tokens(text)
        if current_tokens <= target_tokens:
            return text
        if target_tokens <= 0:
            return ""

        # 计算保留比例
        ratio = target_tokens / current_tokens
        # 保留前 30% 和后 70%（尾部通常更重要，包含最近信息）
        keep_chars = int(len(text) * ratio * 0.8)
        head_chars = int(keep_chars * 0.3)
        tail_chars = keep_chars - head_chars

        compressed = text[:head_chars] + "\n...(已压缩)...\n" + text[-tail_chars:]
        return compressed


class PromptCache:
    """Prompt 缓存：稳定前缀复用，降低延迟和成本。"""

    def __init__(self):
        self._cache: dict[str, str] = {}  # hash -> prefix
        self._hit_count: int = 0
        self._miss_count: int = 0

    def get_cacheable_prefix(self, system_prompt: str, world_context: str,
                             character_context: str) -> str:
        """
        构建可缓存的前缀（系统提示+世界观+角色卡）。
        这些内容在多轮对话中保持稳定，可被 API 缓存复用。
        """
        prefix = f"{system_prompt}\n\n{world_context}\n\n{character_context}"
        prefix_hash = hashlib.md5(prefix.encode()).hexdigest()

        if prefix_hash in self._cache:
            self._hit_count += 1
            logger.debug("Prompt cache hit (hash=%s)", prefix_hash[:8])
        else:
            self._cache[prefix_hash] = prefix
            self._miss_count += 1
            logger.debug("Prompt cache miss (hash=%s)", prefix_hash[:8])

        return prefix

    def get_stats(self) -> dict:
        total = self._hit_count + self._miss_count
        return {
            "hits": self._hit_count,
            "misses": self._miss_count,
            "hit_rate": self._hit_count / max(1, total),
            "cached_prefixes": len(self._cache),
        }


class ContextEngine:
    """上下文引擎：统一管理注意力预算、压缩、缓存。"""

    def __init__(self, max_context_tokens: int = 32000, max_output_tokens: int = 4096):
        self.max_context = max_context_tokens
        self.max_output = max_output_tokens
        self.cache = PromptCache()

    def build_context(
        self,
        system_prompt: str,
        world_context: str,
        character_context: str,
        foreshadow_context: str = "",
        summary_context: str = "",
        rag_context: str = "",
        recent_history: str = "",
        player_input: str = "",
        active_foreshadows: str = "",
    ) -> str:
        """
        构建优化后的上下文。
        按优先级分配 token 预算，压缩低优先级层。
        [v11] active_foreshadows: 活跃伏笔追踪（不可压缩，高优先级）。
        """
        budget = ContextBudget(
            max_tokens=self.max_context - self.max_output
        )

        # 添加各层（priority: 1=最高, 10=最低）
        budget.add_layer(ContextLayer(
            name="system", content=system_prompt, priority=1, compressible=False
        ))
        budget.add_layer(ContextLayer(
            name="world", content=world_context, priority=2, compressible=False
        ))
        budget.add_layer(ContextLayer(
            name="character", content=character_context, priority=3, compressible=False
        ))
        budget.add_layer(ContextLayer(
            name="player_input", content=player_input, priority=2, compressible=False
        ))
        # [v11] P1a: 活跃伏笔追踪 — 不可压缩，确保AI始终知道待回收伏笔
        if active_foreshadows:
            budget.add_layer(ContextLayer(
                name="active_foreshadows", content=active_foreshadows, priority=3,
                compressible=False
            ))
        budget.add_layer(ContextLayer(
            name="foreshadow", content=foreshadow_context, priority=5,
            compressible=True, min_tokens=200
        ))
        budget.add_layer(ContextLayer(
            name="recent", content=recent_history, priority=5,
            compressible=True, min_tokens=500
        ))
        budget.add_layer(ContextLayer(
            name="rag", content=rag_context, priority=6,
            compressible=True, min_tokens=200
        ))
        budget.add_layer(ContextLayer(
            name="summary", content=summary_context, priority=7,
            compressible=True, min_tokens=100
        ))

        allocated = budget.allocate()

        # 组装最终上下文（按稳定→动态顺序，便于前缀缓存）
        parts = []
        for name in ["system", "world", "character", "foreshadow",
                     "active_foreshadows", "summary", "rag", "recent", "player_input"]:
            content = allocated.get(name, "")
            if content:
                parts.append(content)

        final_context = "\n\n---\n\n".join(parts)

        logger.info(
            "Context built: %d layers, %d estimated tokens (budget: %d)",
            len([v for v in allocated.values() if v]),
            _estimate_tokens_shared(final_context),
            budget.available,
        )

        return final_context

    def compress_text(self, text: str, target_tokens: int) -> str:
        """对外暴露的轻量压缩工具，供 NarrativeEngine 等模块复用。"""
        if not text or target_tokens <= 0:
            return ""
        budget = ContextBudget(max_tokens=target_tokens + _estimate_tokens_shared(text))
        return budget._compress(text, target_tokens)

    def get_stats(self) -> dict:
        return {
            "cache": self.cache.get_stats(),
            "max_context": self.max_context,
            "max_output": self.max_output,
        }
