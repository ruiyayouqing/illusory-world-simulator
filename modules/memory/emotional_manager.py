"""[v1.6 P1-8] 情感记忆系统：情感评估器 + 记忆衰减 + NPC 情感状态管理。

设计参考 Plutchik 八情绪轮盘理论：
  joy(喜悦)        trust(信任)      fear(恐惧)      surprise(惊讶)
  sadness(悲伤)   disgust(厌恶)   anger(愤怒)     anticipation(期待)

核心组件：
1. EmotionEvaluator：从文本提取情感（规则优先，LLM 兜底）
2. EmotionalMemoryManager：管理 NPC 情感状态向量、情感强度衰减、对决策的影响
3. NPC 情感状态以 8 维向量表示，每维 0-1，受记忆事件更新并随时间衰减

线程安全：所有 NPC 状态读写通过 RLock 保护，避免 batch_evolve 与玩家交互竞态。
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..llm.base_llm import BaseLLM
    from ..db.chroma_db import MemoryStore

logger = logging.getLogger("chronoverse.memory.emotional")

# ── 8 类情感 + 中文别名（规则匹配用） ─────────────────────────
EMOTION_TYPES: tuple[str, ...] = (
    "joy", "sadness", "anger", "fear",
    "surprise", "disgust", "trust", "anticipation",
)

# 中文情感词表（规则评估器使用，覆盖常见叙事场景）
# 每个情感对应的触发关键词与权重；同一段文本命中多个词时取最大权重
_EMOTION_LEXICON: dict[str, list[tuple[str, float]]] = {
    "joy": [
        ("欣喜", 0.95), ("狂喜", 1.0), ("喜极而泣", 1.0), ("开心", 0.8),
        ("欢笑", 0.7), ("笑出声", 0.85), ("愉悦", 0.7), ("快乐", 0.75),
        ("欣慰", 0.8), ("甜蜜", 0.75), ("幸福", 0.85), ("大喜", 0.95),
        ("雀跃", 0.85), ("眉开眼笑", 0.8), ("欣喜若狂", 1.0),
    ],
    "sadness": [
        ("悲伤", 0.85), ("悲痛", 0.95), ("哀恸", 1.0), ("心如刀割", 0.95),
        ("泪流满面", 0.9), ("痛哭", 0.9), ("哭泣", 0.7), ("落泪", 0.7),
        ("沮丧", 0.7), ("绝望", 0.9), ("心碎", 0.9), ("怅然", 0.6),
        ("黯然", 0.65), ("哀伤", 0.8), ("凄凉", 0.75), ("痛失", 0.85),
    ],
    "anger": [
        ("暴怒", 1.0), ("怒不可遏", 0.95), ("愤怒", 0.85), ("怒火", 0.85),
        ("咬牙切齿", 0.8), ("拍案而起", 0.85), ("杀意", 0.9), ("仇恨", 0.85),
        ("愤恨", 0.8), ("怒视", 0.75), ("喝骂", 0.75), ("咒骂", 0.7),
        ("雷霆大怒", 0.95), ("目眦欲裂", 0.95),
    ],
    "fear": [
        ("恐惧", 0.85), ("惊恐", 0.9), ("战栗", 0.85), ("颤抖", 0.75),
        ("毛骨悚然", 0.95), ("不寒而栗", 0.9), ("骇然", 0.85), ("胆寒", 0.8),
        ("惶恐", 0.8), ("惊惧", 0.85), ("退避三舍", 0.7), ("如临大敌", 0.7),
        ("丧胆", 0.85), ("惊魂未定", 0.85),
    ],
    "surprise": [
        ("震惊", 0.9), ("大惊", 0.85), ("愕然", 0.8), ("惊诧", 0.8),
        ("难以置信", 0.85), ("目瞪口呆", 0.9), ("惊呼", 0.75), ("骇异", 0.8),
        ("意外", 0.6), ("出乎意料", 0.7), ("瞠目", 0.75),
    ],
    "disgust": [
        ("厌恶", 0.85), ("恶心", 0.85), ("作呕", 0.8), ("鄙夷", 0.8),
        ("嫌弃", 0.75), ("不屑", 0.7), ("唾弃", 0.8), ("痛恨", 0.85),
        ("鄙薄", 0.75), ("反感", 0.75),
    ],
    "trust": [
        ("信赖", 0.85), ("信任", 0.85), ("托付", 0.85), ("倚重", 0.8),
        ("深信不疑", 0.95), ("肝胆相照", 0.95), ("推心置腹", 0.9),
        ("信服", 0.75), ("倚仗", 0.75), ("倾心", 0.7), ("追随", 0.7),
    ],
    "anticipation": [
        ("期待", 0.8), ("期盼", 0.85), ("翘首以盼", 0.9), ("渴望", 0.85),
        ("盼望", 0.8), ("企盼", 0.8), ("渴望已久", 0.9), ("蓄势待发", 0.8),
        ("跃跃欲试", 0.8), ("筹谋", 0.7),
    ],
}

# 效价映射：积极(+1) ~ 消极(-1)
_VALENCE_MAP: dict[str, float] = {
    "joy": 0.8, "trust": 0.6, "anticipation": 0.4, "surprise": 0.0,
    "sadness": -0.8, "anger": -0.7, "fear": -0.6, "disgust": -0.7,
}

# 唤醒度映射：平静(0) ~ 激动(1)
_AROUSAL_MAP: dict[str, float] = {
    "joy": 0.7, "anger": 0.9, "fear": 0.85, "surprise": 0.85,
    "sadness": 0.3, "disgust": 0.5, "trust": 0.3, "anticipation": 0.65,
}

# 情感对（Plutchic 互补/对立对）
_EMOTION_OPPOSITES: dict[str, str] = {
    "joy": "sadness", "sadness": "joy",
    "anger": "trust", "trust": "anger",
    "fear": "anger",  # fear 抑制 anger
    "surprise": "anticipation", "anticipation": "surprise",
    "disgust": "joy",  # disgust 与 joy 对立
}


class EmotionEvaluator:
    """情感评估器：从文本提取主导情感与强度。

    默认走规则（基于中文情感词表，零成本），可选注入 LLM 用于复杂叙事。
    LLM 失败时自动回退到规则结果。
    """

    def __init__(self, llm: "BaseLLM | None" = None,
                 use_llm_threshold: int = 200):
        """
        - llm: 可选的备用模型，当文本长度超过 use_llm_threshold 且规则未命中时调用
        - use_llm_threshold: 触发 LLM 的最小文本长度
        """
        self.llm = llm
        self.use_llm_threshold = use_llm_threshold
        # 预编译规则匹配模式：emotion -> compiled regex list
        self._compiled: dict[str, list[tuple[re.Pattern, float]]] = {}
        for emo, pairs in _EMOTION_LEXICON.items():
            self._compiled[emo] = [(re.compile(re.escape(kw)), w) for kw, w in pairs]

    def evaluate(self, text: str) -> dict:
        """
        评估文本的情感。

        返回：
          {
            "emotion_type": str,        # 8 类之一，无情感返回 "neutral"
            "emotional_weight": float,  # 0-1
            "valence": float,           # -1 ~ +1
            "arousal": float,           # 0 ~ 1
            "all_scores": dict,         # 8 类情感各自的命中权重
            "source": str,              # "rule" | "llm" | "neutral"
          }
        """
        if not text or not text.strip():
            return self._neutral()
        scores = self._rule_evaluate(text)
        # 规则命中且文本较短：直接返回
        top_emo, top_score = max(scores.items(), key=lambda x: x[1])
        if top_score > 0 and len(text) < self.use_llm_threshold:
            return self._format_result(top_emo, top_score, scores, "rule")
        # 文本较长且规则未命中或较弱：尝试 LLM
        if self.llm is not None and (top_score == 0 or len(text) >= self.use_llm_threshold):
            llm_result = self._llm_evaluate(text)
            if llm_result is not None:
                emo = llm_result.get("emotion_type", "neutral").lower()
                if emo in EMOTION_TYPES or emo == "neutral":
                    weight = float(llm_result.get("emotional_weight", 0.0) or 0.0)
                    if emo != "neutral" and weight > 0:
                        # 合并 LLM 与规则结果（取较高者）
                        merged = dict(scores)
                        merged[emo] = max(merged.get(emo, 0.0), weight)
                        new_top_emo, new_top_score = max(
                            merged.items(), key=lambda x: x[1])
                        return self._format_result(
                            new_top_emo, new_top_score, merged, "llm")
        if top_score > 0:
            return self._format_result(top_emo, top_score, scores, "rule")
        return self._neutral()

    # ── 内部实现 ────────────────────────────────────────────

    def _rule_evaluate(self, text: str) -> dict[str, float]:
        scores: dict[str, float] = {e: 0.0 for e in EMOTION_TYPES}
        for emo, patterns in self._compiled.items():
            max_w = 0.0
            for pat, weight in patterns:
                if pat.search(text):
                    if weight > max_w:
                        max_w = weight
            scores[emo] = max_w
        return scores

    def _llm_evaluate(self, text: str) -> dict | None:
        if self.llm is None:
            return None
        try:
            prompt = (
                "请分析以下叙事文本的情感倾向，按 Plutchik 八情绪模型返回 JSON。\n"
                "字段：emotion_type(joy/sadness/anger/fear/surprise/disgust/trust/anticipation/neutral)，"
                "emotional_weight(0-1)，valence(-1~+1)，arousal(0~1)。\n"
                "仅返回 JSON，无解释。\n\n"
                f"文本：\n{text[:800]}"
            )
            resp = self.llm.chat_json(prompt, temperature=0.2)
            return resp
        except Exception as e:
            logger.debug("LLM emotion evaluate failed: %s", e)
            return None

    def _format_result(self, emotion: str, score: float,
                       all_scores: dict, source: str) -> dict:
        return {
            "emotion_type": emotion,
            "emotional_weight": round(min(1.0, score), 3),
            "valence": _VALENCE_MAP.get(emotion, 0.0),
            "arousal": _AROUSAL_MAP.get(emotion, 0.5),
            "all_scores": {k: round(v, 3) for k, v in all_scores.items()},
            "source": source,
        }

    def _neutral(self) -> dict:
        return {
            "emotion_type": "neutral",
            "emotional_weight": 0.0,
            "valence": 0.0,
            "arousal": 0.3,
            "all_scores": {e: 0.0 for e in EMOTION_TYPES},
            "source": "neutral",
        }


# ── NPC 情感状态向量 ─────────────────────────────────────────
class NPCEmotionState:
    """单个 NPC 的情感状态向量。

    - vector: 8 维向量（emotion_type -> intensity 0-1）
    - decay: 每次回合调用 decay() 让强度自然衰减
    - history: 最近 N 次情感事件（用于追溯）
    """

    __slots__ = ("npc_id", "npc_name", "vector", "history",
                 "last_update_turn", "_lock")

    def __init__(self, npc_id: str, npc_name: str = ""):
        self.npc_id = npc_id
        self.npc_name = npc_name
        self.vector: dict[str, float] = {e: 0.0 for e in EMOTION_TYPES}
        # 最近 20 条情感事件，用于追溯
        self.history: deque = deque(maxlen=20)
        self.last_update_turn: int = 0
        self._lock = threading.RLock()

    def update(self, emotion: str, intensity: float,
               turn: int, source: str = "event", detail: str = ""):
        """累加情感事件到状态向量。

        高强度情感会同时抑制其对立情感（Plutchik 对立对）。
        采用"峰值+平滑"累积：new = max(old * 0.7 + intensity * 0.3, intensity * 0.5)。
        - 平滑项避免情绪剧烈跳变；
        - 峰值项保证单次强烈事件能立即达到中等阈值（0.5*intensity），
          随后随 decay() 自然衰减，避免"强事件无感"。
        """
        if emotion not in EMOTION_TYPES:
            return
        intensity = max(0.0, min(1.0, intensity))
        with self._lock:
            smoothed = self.vector[emotion] * 0.7 + intensity * 0.3
            peak_floor = intensity * 0.5
            self.vector[emotion] = min(1.0, max(smoothed, peak_floor))
            # 抑制对立情感
            opposite = _EMOTION_OPPOSITES.get(emotion)
            if opposite and opposite in self.vector:
                self.vector[opposite] = max(
                    0.0, self.vector[opposite] - intensity * 0.2)
            self.last_update_turn = turn
            self.history.append({
                "turn": turn,
                "emotion": emotion,
                "intensity": round(intensity, 3),
                "source": source,
                "detail": detail[:100],
                "ts": time.time(),
            })

    def decay(self, decay_rate: float = 0.05):
        """每回合衰减所有情感强度（向 0 衰减）。"""
        with self._lock:
            for emo in self.vector:
                self.vector[emo] = max(0.0, self.vector[emo] - decay_rate)

    def dominant_emotion(self) -> tuple[str, float]:
        """返回当前主导情感与强度。"""
        with self._lock:
            if not any(self.vector.values()):
                return ("neutral", 0.0)
            emo, intensity = max(self.vector.items(), key=lambda x: x[1])
            return (emo, round(intensity, 3))

    def valence(self) -> float:
        """计算综合效价（-1 ~ +1）。"""
        with self._lock:
            total = 0.0
            weight_sum = 0.0
            for emo, v in self.vector.items():
                val = _VALENCE_MAP.get(emo, 0.0)
                total += val * v
                weight_sum += v
            if weight_sum <= 0:
                return 0.0
            return round(total / weight_sum, 3)

    def arousal(self) -> float:
        """计算综合唤醒度（0 ~ 1）。"""
        with self._lock:
            total = 0.0
            weight_sum = 0.0
            for emo, v in self.vector.items():
                ar = _AROUSAL_MAP.get(emo, 0.5)
                total += ar * v
                weight_sum += v
            if weight_sum <= 0:
                return 0.3
            return round(total / weight_sum, 3)

    def to_dict(self) -> dict:
        with self._lock:
            emo, intensity = self.dominant_emotion()
            return {
                "npc_id": self.npc_id,
                "npc_name": self.npc_name,
                "vector": {k: round(v, 3) for k, v in self.vector.items()},
                "dominant_emotion": emo,
                "dominant_intensity": intensity,
                "valence": self.valence(),
                "arousal": self.arousal(),
                "last_update_turn": self.last_update_turn,
                "history": list(self.history),
            }


# ── 情感记忆管理器 ──────────────────────────────────────────
class EmotionalMemoryManager:
    """[v1.6 P1-8] 情感记忆管理器。

    职责：
    1. 评估文本情感并写入 MemoryStore（带 emotion_type 标记）
    2. 维护每个 NPC 的情感状态向量（NPCEmotionState）
    3. 计算情感强度衰减
    4. 生成情感提示文本注入 NPC/Player 决策 prompt

    使用方式：
        mgr = EmotionalMemoryManager(llm=..., memory_store=...)
        mgr.record_event(text, npc_ids=["npc_01"], turn=10)
        hint = mgr.get_npc_emotion_hint(npc_id="npc_01")
    """

    # 情感强度影响对话风格的阈值
    HIGH_INTENSITY_THRESHOLD = 0.6
    MEDIUM_INTENSITY_THRESHOLD = 0.3

    # 默认衰减率（每回合）
    DEFAULT_DECAY_RATE = 0.05

    def __init__(self, llm: "BaseLLM | None" = None,
                 memory_store: "MemoryStore | None" = None,
                 decay_rate: float = DEFAULT_DECAY_RATE):
        self.evaluator = EmotionEvaluator(llm=llm)
        self.memory_store = memory_store
        self.decay_rate = decay_rate
        # NPC 情感状态表：npc_id -> NPCEmotionState
        self._npc_states: dict[str, NPCEmotionState] = {}
        # 玩家情感状态（单一）
        self._player_state: NPCEmotionState | None = None
        self._lock = threading.RLock()

    def set_memory_store(self, memory_store: "MemoryStore"):
        """延迟注入 MemoryStore（与世界加载同步）。"""
        self.memory_store = memory_store

    # ── 事件记录 ────────────────────────────────────────────

    def record_event(self, text: str,
                     npc_ids: list[str] = None,
                     npc_names: list[str] = None,
                     turn: int = 0,
                     source: str = "narrative",
                     importance: float = 0.5,
                     detail: str = "") -> dict:
        """
        记录一段叙事事件：评估情感 → 写入记忆库 → 更新相关 NPC 情感状态。

        返回评估结果 dict（含 emotion_type, emotional_weight 等）。
        """
        # 1. 情感评估
        result = self.evaluator.evaluate(text)
        emo = result["emotion_type"]
        weight = result["emotional_weight"]
        # 2. 写入记忆库（仅有效情感才写入，避免污染）
        if (self.memory_store is not None and emo != "neutral"
                and weight > 0.1):
            try:
                related = []
                if npc_names:
                    related.extend(npc_names)
                self.memory_store.add_emotional_memory(
                    text=text,
                    emotion_type=emo,
                    emotional_weight=weight,
                    valence=result["valence"],
                    arousal=result["arousal"],
                    importance=importance,
                    related_entities=related,
                )
            except Exception as e:
                logger.debug("add_emotional_memory failed: %s", e)
        # 3. 更新 NPC 情感状态
        # [Bug fix] 原先对整段文本做一次情感评估，然后把同一个情感套用到所有被提到的
        # NPC + 主角，导致同一回合内被提到的所有 NPC 情感状态趋同、且与主角一致。
        # 修复：对每个 NPC 提取其名字附近的文本片段单独评估情感，使不同 NPC
        # 能有不同的情感反应（如 A 愤怒、B 恐惧）。
        if npc_ids and emo != "neutral" and weight > 0.1:
            for i, nid in enumerate(npc_ids):
                name = npc_names[i] if npc_names and i < len(npc_names) else ""
                # 对每个 NPC 提取相关片段单独评估
                npc_text = self._extract_npc_context(text, name) if name else text
                if npc_text:
                    npc_eval = self.evaluator.evaluate(npc_text)
                    npc_emo = npc_eval.get("emotion_type", emo)
                    npc_weight = npc_eval.get("emotional_weight", weight)
                else:
                    npc_emo, npc_weight = emo, weight
                if npc_emo != "neutral" and npc_weight > 0.1:
                    state = self._get_or_create_npc_state(nid, name)
                    state.update(npc_emo, npc_weight, turn, source, detail)
        # 4. 同时更新玩家情感状态（玩家也是叙事主体）
        if emo != "neutral" and weight > 0.1:
            ps = self._get_or_create_player_state()
            ps.update(emo, weight, turn, source, detail)
        return result

    def decay_all(self, turn: int):
        """每回合对所有 NPC 情感状态衰减。"""
        with self._lock:
            for state in self._npc_states.values():
                state.decay(self.decay_rate)
            if self._player_state is not None:
                self._player_state.decay(self.decay_rate)

    # ── 查询 ────────────────────────────────────────────────

    def get_npc_emotion(self, npc_id: str) -> dict | None:
        """获取 NPC 当前情感状态。"""
        with self._lock:
            state = self._npc_states.get(npc_id)
            return state.to_dict() if state else None

    def get_player_emotion(self) -> dict:
        """获取玩家情感状态。"""
        with self._lock:
            if self._player_state is None:
                return {"dominant_emotion": "neutral", "dominant_intensity": 0.0,
                        "vector": {e: 0.0 for e in EMOTION_TYPES}}
            return self._player_state.to_dict()

    def get_all_npc_emotions(self) -> list[dict]:
        """获取所有 NPC 的情感状态（用于前端面板可视化）。"""
        with self._lock:
            return [s.to_dict() for s in self._npc_states.values()]

    def get_emotional_summary(self, related_entity: str = None) -> dict:
        """转发到 MemoryStore.get_emotional_summary。"""
        if self.memory_store is None:
            return {"emotions": {}, "total": 0, "avg_valence": 0.0}
        return self.memory_store.get_emotional_summary(related_entity)

    # ── 决策 prompt 注入 ────────────────────────────────────

    def get_npc_emotion_hint(self, npc_id: str,
                              npc_name: str = "") -> str:
        """生成 NPC 决策 prompt 的情感状态提示文本。

        强度低于 MEDIUM_INTENSITY_THRESHOLD 时不注入（情感平淡时不打扰决策）。
        """
        state = self._npc_states.get(npc_id)
        if state is None:
            return ""
        emo, intensity = state.dominant_emotion()
        if emo == "neutral" or intensity < self.MEDIUM_INTENSITY_THRESHOLD:
            return ""
        valence = state.valence()
        arousal = state.arousal()
        # 高强度情感对话风格提示
        style_hint = self._get_style_hint(emo, intensity)
        hint = (
            f"\n【该角色当前情感状态】\n"
            f"主导情感：{emo}（强度 {intensity:.0%}）\n"
            f"效价：{valence:+.2f}（{'积极' if valence >= 0 else '消极'}），"
            f"唤醒度：{arousal:.0%}（{'激动' if arousal >= 0.7 else '平静'}）\n"
            f"风格提示：{style_hint}"
        )
        return hint

    def get_player_emotion_hint(self) -> str:
        """生成玩家决策 prompt 的情感状态提示。"""
        if self._player_state is None:
            return ""
        emo, intensity = self._player_state.dominant_emotion()
        if emo == "neutral" or intensity < self.MEDIUM_INTENSITY_THRESHOLD:
            return ""
        valence = self._player_state.valence()
        hint = (
            f"\n【主角当前情感状态】\n"
            f"主导情感：{emo}（强度 {intensity:.0%}），"
            f"效价：{valence:+.2f}\n"
            f"请在叙事中体现主角的这种情感倾向，但不要直白地说出情感词。"
        )
        return hint

    # ── 内部工具 ────────────────────────────────────────────

    def _get_style_hint(self, emotion: str, intensity: float) -> str:
        """根据情感类型与强度生成对话风格提示。"""
        high = intensity >= self.HIGH_INTENSITY_THRESHOLD
        hints = {
            "joy": (
                "情绪高涨，言谈中带笑，语气轻快，对他人更宽容友善"
                if high else "心情尚可，语气略带温度"
            ),
            "sadness": (
                "情绪低落，言辞间流露哀愁，回应简短迟疑"
                if high else "心境略显低落，言辞稍显萧索"
            ),
            "anger": (
                "怒火难抑，语气生硬尖锐，易与人起冲突"
                if high else "心中有气，言辞略带锋芒"
            ),
            "fear": (
                "惊魂未定，言行谨慎多疑，易做出保守选择"
                if high else "心存忌惮，行事略带犹疑"
            ),
            "surprise": (
                "大为惊愕，言行失措，反应变得迟钝"
                if high else "略感意外，言行稍有迟疑"
            ),
            "disgust": (
                "心生厌恶，对相关人事物避之不及，言辞带刺"
                if high else "略感不适，态度冷淡疏离"
            ),
            "trust": (
                "深信不疑，愿意托付重要事务，态度诚恳"
                if high else "心存信赖，态度较为温和"
            ),
            "anticipation": (
                "满怀期待，言谈中流露憧憬，行动积极"
                if high else "心有所期，行事略带朝气"
            ),
        }
        return hints.get(emotion, "")

    def _extract_npc_context(self, text: str, npc_name: str,
                             window: int = 60) -> str:
        """提取 NPC 名字附近的文本片段，用于单独评估该 NPC 的情感。

        策略（优先级递减）：
        1. 句子级分割：按句号/感叹号/问号分割，取包含 NPC 名字的句子。
           这样不同 NPC 即使出现在同一短文本中也能拿到不同片段。
        2. 窗口回退：若无标点分割，取 NPC 名字前后各 window 字符。
        3. 整段回退：若名字未出现或文本过短，返回整段。

        例如叙事「小明愤怒地拍了桌子。小红吓得后退。」：
        - 对"小明"提取 → "小明愤怒地拍了桌子。" → 评估为 anger
        - 对"小红"提取 → "小红吓得后退。" → 评估为 fear
        """
        if not text or not npc_name:
            return text or ""
        # 1. 句子级分割：按中英文句末标点分割
        import re as _re
        # 按句号/感叹号/问号分割，保留分隔符
        sentences = _re.split(r'(?<=[。！？!?])', text)
        # 收集包含 NPC 名字的句子
        matched = [s for s in sentences if s.strip() and npc_name in s]
        if matched:
            return "".join(matched[:3])
        # 2. 窗口回退：文本较长且名字出现时，取前后 window 字符
        if len(text) > window * 2:
            snippets: list[str] = []
            start = 0
            while True:
                idx = text.find(npc_name, start)
                if idx < 0:
                    break
                s = max(0, idx - window)
                e = min(len(text), idx + len(npc_name) + window)
                snippets.append(text[s:e])
                start = idx + len(npc_name)
                if len(snippets) >= 3:
                    break
            if snippets:
                return "".join(snippets)
        # 3. 整段回退
        return text

    def _get_or_create_npc_state(self, npc_id: str,
                                  npc_name: str = "") -> NPCEmotionState:
        with self._lock:
            state = self._npc_states.get(npc_id)
            if state is None:
                state = NPCEmotionState(npc_id, npc_name or npc_id)
                self._npc_states[npc_id] = state
            elif npc_name and not state.npc_name:
                state.npc_name = npc_name
            return state

    def _get_or_create_player_state(self) -> NPCEmotionState:
        with self._lock:
            if self._player_state is None:
                self._player_state = NPCEmotionState("player", "主角")
            return self._player_state

    def bind_npc_name(self, npc_id: str, npc_name: str):
        """补齐 NPC 名称（懒加载场景）。"""
        with self._lock:
            state = self._npc_states.get(npc_id)
            if state and not state.npc_name:
                state.npc_name = npc_name


# ── 全局单例（与 memory_audit_log 模式一致） ─────────────────
_emotional_manager: EmotionalMemoryManager | None = None


def get_emotional_manager() -> EmotionalMemoryManager | None:
    """获取全局情感记忆管理器单例。"""
    return _emotional_manager


def set_emotional_manager(mgr: EmotionalMemoryManager):
    """设置全局单例（由 GameEngine 在服务初始化时调用）。"""
    global _emotional_manager
    _emotional_manager = mgr
