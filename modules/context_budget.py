from __future__ import annotations
import logging
import math
import re

logger = logging.getLogger("chronoverse.context_budget")

# ── 与 context_engine 的协同关系 ──────────────────────────
# 本模块提供「场景感知 + 按比例分配」的上下文预算管理（ContextBudgetManager /
# build_system_context_with_budget），以及共享的 estimate_tokens 估算函数。
#
# v10++ 新增的 context_engine.ContextEngine 在此基础上提供「按优先级分配 +
# 轻量压缩 + Prompt Caching」的升级能力：
#   - ContextEngine 复用本模块的 estimate_tokens，保证 token 口径一致；
#   - 当 ContextEngine 可用（已注入 PlayerAgent）时，优先走优先级裁剪/压缩路径；
#   - 当 ContextEngine 不可用或异常时，回退到本模块的 build_system_context_with_budget。
# 两套体系并存，互不破坏，逐步迁移。

# ── 场景预算配置 ──────────────────────────────────────────
# 根据不同场景动态调整各层 token 分配比例

SCENE_BUDGETS: dict[str, dict[str, float]] = {
    "combat": {
        "world": 0.15, "npc": 0.20, "player": 0.30,
        "history": 0.08, "lore": 0.15, "rag": 0.05, "fixed": 0.07,
    },
    "social": {
        "world": 0.08, "npc": 0.35, "player": 0.15,
        "history": 0.18, "lore": 0.10, "rag": 0.08, "fixed": 0.06,
    },
    "exploration": {
        "world": 0.28, "npc": 0.10, "player": 0.15,
        "history": 0.08, "lore": 0.25, "rag": 0.08, "fixed": 0.06,
    },
    "trading": {
        "world": 0.18, "npc": 0.15, "player": 0.20,
        "history": 0.10, "lore": 0.15, "rag": 0.12, "fixed": 0.10,
    },
    "rest": {
        "world": 0.10, "npc": 0.10, "player": 0.20,
        "history": 0.25, "lore": 0.10, "rag": 0.15, "fixed": 0.10,
    },
    "default": {
        "world": 0.15, "npc": 0.20, "player": 0.20,
        "history": 0.15, "lore": 0.15, "rag": 0.10, "fixed": 0.05,
    },
}

# 场景检测关键词
_SCENE_KEYWORDS: dict[str, list[str]] = {
    "combat":     ["战斗", "攻击", "杀", "打", "战", "剑", "刀", "拳", "武", "厮杀",
                   "对战", "危险", "逃跑", "防御", "格挡"],
    "social":     ["对话", "交谈", "聊天", "谈话", "倾诉", "商议", "劝说", "约会",
                   "拜访", "宴请", "结交", "谈判"],
    "exploration": ["探索", "搜索", "寻找", "调查", "查看", "观察", "研究",
                   "发现", "进入", "前进", "冒险"],
    "trading":    ["交易", "买卖", "购买", "出售", "讲价", "市场", "商人",
                   "货物", "金币", "财富"],
    "rest":       ["休息", "睡觉", "歇", "安歇", "入眠", "养神", "静坐",
                   "恢复", "疗伤", "修炼"],
}


def detect_scene(player_input: str, narrative_history: list = None) -> str:
    """检测当前场景类型，用于动态分配上下文预算"""
    text = player_input.lower()
    scores: dict[str, int] = {}
    for scene, keywords in _SCENE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scores[scene] = score

    # 参考最近的历史叙事
    if narrative_history:
        recent = narrative_history[-3:]
        for entry in recent:
            hist_text = entry.get("text", "")[:200].lower()
            for scene, keywords in _SCENE_KEYWORDS.items():
                score = sum(1 for kw in keywords if kw in hist_text)
                scores[scene] = scores.get(scene, 0) + score * 0.5

    if not scores:
        return "default"
    return max(scores, key=scores.get)


def estimate_tokens(text: str) -> int:
    """估算 token 数。优先用 tiktoken 精确计算，回退到启发式估算。"""
    if not text:
        return 0
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except ImportError:
        pass
    cn_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other = len(text) - cn_chars
    return math.ceil(cn_chars * 1.5 + other * 0.3)


class ContextBudgetManager:
    """管理 LLM 上下文的 token 预算分配"""

    def __init__(self, max_context: int = 8192, max_output: int = 4096):
        self.max_context = max_context
        self.max_output = max_output
        self.reserved = 0
        self.allocations: dict[str, int] = {}

    def reset(self):
        self.reserved = 0
        self.allocations = {}

    def set_limits(self, max_context: int, max_output: int):
        self.max_context = max_context
        self.max_output = max_output

    @property
    def available(self) -> int:
        return max(0, self.max_context - self.max_output - self.reserved)

    def reserve(self, name: str, text: str) -> str:
        tokens = estimate_tokens(text)
        if self.reserved + tokens > self.max_context - self.max_output:
            remaining = max(0, self.max_context - self.max_output - self.reserved)
            if remaining < 50:
                logger.debug("Budget exhausted at %d/%d tokens", self.reserved, self.max_context)
                return ""
            ratio = remaining / tokens
            truncated = text[:int(len(text) * ratio * 0.9)]
            tokens = estimate_tokens(truncated)
            self.reserved += tokens
            self.allocations[name] = tokens
            logger.debug("Truncated '%s' to %d tokens", name, tokens)
            return truncated
        self.reserved += tokens
        self.allocations[name] = tokens
        return text

    def can_fit(self, text: str) -> bool:
        tokens = estimate_tokens(text)
        return self.reserved + tokens <= self.max_context - self.max_output

    def get_budget_report(self) -> dict:
        return {
            "max_context": self.max_context,
            "max_output": self.max_output,
            "reserved": self.reserved,
            "available": self.available,
            "utilization": round(self.reserved / max(1, self.max_context) * 100, 1),
            "allocations": dict(self.allocations),
        }


def build_system_context_with_budget(
    system_prompt: str,
    world_text: str,
    npc_text: str,
    history_text: str,
    lorebook_text: str,
    rag_text: str,
    player_text: str,
    fixed_prompt: str = "",
    max_context: int = 32768,
    max_output: int = 4096,
    identity_text: str = "",
    scene: str = "default",
    graph_rag_text: str = "",
) -> str:
    """
    使用预算管理构建系统上下文，确保不溢出。
    
    支持动态场景感知：根据 scene 类型调整各层 token 分配比例。
    新增 identity_text（长期身份记忆）和 graph_rag_text（知识图谱检索）。
    """
    bm = ContextBudgetManager(max_context, max_output)
    budget = SCENE_BUDGETS.get(scene, SCENE_BUDGETS["default"])

    # 可用 token 总量（减去输出预留）
    available = max_context - max_output
    parts = []

    # 按场景预算比例排序各层
    layers = [
        ("system_prompt", system_prompt, 0.0),  # 系统提示词不受场景影响
        ("world", world_text, budget.get("world", 0.15)),
        ("npc", npc_text, budget.get("npc", 0.20)),
        ("player", player_text, budget.get("player", 0.20)),
        ("identity", identity_text, 0.08),  # 身份记忆固定 8%
        ("lorebook", lorebook_text, budget.get("lore", 0.15)),
        ("rag", rag_text, budget.get("rag", 0.10)),
        ("graph_rag", graph_rag_text, 0.05),  # 知识图谱固定 5%
        ("history", history_text, budget.get("history", 0.15)),
        ("fixed_prompt", fixed_prompt, budget.get("fixed", 0.05)),
    ]

    for name, text, ratio in layers:
        if not text:
            continue
        # 系统提示词和固定提示词不限比例
        if name in ("system_prompt", "fixed_prompt"):
            reserved = bm.reserve(name, text)
        else:
            # 按场景比例限制最大 token 数
            max_tokens_for_layer = int(available * ratio * 1.5)  # 允许 50% 弹性
            tokens = estimate_tokens(text)
            if tokens > max_tokens_for_layer and max_tokens_for_layer > 0:
                # 截断到比例限制
                trunc_ratio = max_tokens_for_layer / tokens
                truncated = text[:int(len(text) * trunc_ratio * 0.95)]
                # 统一走 bm.reserve() 容量检查，避免手动累加绕过预算上限
                reserved = bm.reserve(name, truncated)
                logger.debug("Scene '%s': truncated '%s' to %d tokens (ratio %.0f%%)",
                             scene, name, bm.allocations.get(name, 0), ratio * 100)
            else:
                reserved = bm.reserve(name, text)
        if reserved:
            parts.append(reserved)

    logger.info(
        "Context budget [scene=%s]: %d/%d tokens used (%.1f%%), allocations: %s",
        scene, bm.reserved, bm.max_context,
        bm.reserved / max(1, bm.max_context) * 100,
        {k: v for k, v in bm.allocations.items()},
    )

    return "\n\n".join(parts)
