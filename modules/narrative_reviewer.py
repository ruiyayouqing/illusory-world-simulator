"""
[v10] 闭环学习系统 — 叙事回顾 + 教训提取

核心思路（借鉴 Hermes Agent 的"后台自我改进审查"）：
  1. 每 N 回合触发一次"叙事回顾"
  2. 检查最近叙事的一致性（人物性格前后矛盾？事件时间线合理？）
  3. 提取"教训"存入长期记忆（如"该玩家偏好武侠风格，不喜欢科幻元素"）
  4. 动态调整叙事提示词模板
  5. 记录叙事质量指标，支持趋势分析

设计原则：
  - 回顾是异步的、低成本的，不阻塞主游戏循环
  - 教训分为三类：玩家偏好、叙事质量、世界一致性
  - 教训有重要性评分和过期机制
"""
from __future__ import annotations
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from .prompt_utils import resolve_location_name  # [Bug] location code → display name

if TYPE_CHECKING:
    from .llm.base_llm import BaseLLM

logger = logging.getLogger("chronoverse.narrative_reviewer")


@dataclass
class Lesson:
    """一条从叙事回顾中提取的教训"""
    lesson_id: str
    category: str        # "player_preference" / "narrative_quality" / "world_consistency"
    content: str         # 教训内容
    importance: float    # 0.0-1.0
    created_turn: int
    created_day: int
    times_applied: int = 0
    last_applied_turn: int = 0
    evidence: list[str] = field(default_factory=list)  # 支撑证据

    def to_dict(self) -> dict:
        return {
            "lesson_id": self.lesson_id,
            "category": self.category,
            "content": self.content,
            "importance": self.importance,
            "created_turn": self.created_turn,
            "created_day": self.created_day,
            "times_applied": self.times_applied,
            "last_applied_turn": self.last_applied_turn,
            "evidence": self.evidence[-5:],  # 只保留最近5条证据
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Lesson":
        from .schemas import safe_dataclass_from_dict
        return safe_dataclass_from_dict(cls, d)


@dataclass
class NarrativeQualityMetrics:
    """叙事质量指标"""
    consistency_score: float = 1.0      # 一致性评分 0-1
    engagement_score: float = 0.5       # 参与度评分 0-1
    pacing_score: float = 0.5           # 节奏评分 0-1
    character_depth_score: float = 0.5  # 角色深度评分 0-1
    review_count: int = 0

    @property
    def overall_score(self) -> float:
        weights = [0.3, 0.25, 0.2, 0.25]
        scores = [self.consistency_score, self.engagement_score,
                  self.pacing_score, self.character_depth_score]
        return sum(w * s for w, s in zip(weights, scores))

    def to_dict(self) -> dict:
        return {
            "consistency_score": round(self.consistency_score, 3),
            "engagement_score": round(self.engagement_score, 3),
            "pacing_score": round(self.pacing_score, 3),
            "character_depth_score": round(self.character_depth_score, 3),
            "overall_score": round(self.overall_score, 3),
            "review_count": self.review_count,
        }


class NarrativeReviewer:
    """
    [v10] 叙事回顾器 — 闭环学习系统的核心

    借鉴 Hermes Agent 的"后台自我改进审查"机制：
    - 定期回顾最近的叙事
    - 提取教训（玩家偏好、叙事质量、世界一致性）
    - 教训注入到后续的叙事生成中
    - 质量指标追踪，支持趋势分析
    """

    def __init__(self, llm: "BaseLLM", review_interval: int = 5):
        self.llm = llm
        self.review_interval = review_interval  # 每 N 回合回顾一次
        self.lessons: list[Lesson] = []
        self.quality_metrics = NarrativeQualityMetrics()
        self._lesson_counter = 0
        self._last_review_turn = 0
        self._review_history: list[dict] = []  # 回顾历史

    def should_review(self, current_turn: int) -> bool:
        """判断是否应该触发回顾"""
        return current_turn - self._last_review_turn >= self.review_interval

    def review(self, recent_narratives: list[dict],
               player_state, world_state, npc_states: dict,
               current_turn: int, current_day: int) -> dict:
        """
        执行一次叙事回顾。

        Args:
            recent_narratives: 最近的叙事历史列表
            player_state: 玩家状态
            world_state: 世界状态
            npc_states: NPC状态字典
            current_turn: 当前回合
            current_day: 当前天数

        Returns:
            回顾结果字典
        """
        if not recent_narratives:
            return {"reviewed": False, "reason": "no_narratives"}

        self._last_review_turn = current_turn

        # 构建回顾上下文
        narrative_text = "\n".join([
            f"[第{n.get('day', '?')}天] {n.get('text', '')[:300]}"
            for n in recent_narratives[-10:]
        ])

        npc_info = "\n".join([
            f"- {npc.name}: 性格={npc.personality[:30]}, "
            f"角色={npc.role}, 好感={npc.relation_to_player.favor}"
            for npc in list(npc_states.values())[:10]
        ]) if npc_states else "无NPC信息"

        player_info = (
            f"姓名={player_state.name}, 年龄={player_state.age}, "
            f"位置={resolve_location_name(player_state.location, world_state)}, "  # [Bug] location code → display name
            f"标签={', '.join(player_state.tags[:8])}, "
            f"目标={player_state.current_goal}"
        ) if player_state else "无玩家信息"

        # 收集玩家偏好线索
        preference_hints = ""
        if player_state and player_state.memory:
            recent_mem = player_state.memory.short_term[-5:]
            if recent_mem:
                preference_hints = "近期记忆:\n" + "\n".join(
                    f"- {m}" for m in recent_mem
                )

        prompt = f"""你是叙事质量审查员。请回顾最近的叙事，提取教训和改进建议。

【最近叙事】
{narrative_text}

【玩家信息】
{player_info}

【NPC信息】
{npc_info}

【玩家近期记忆】
{preference_hints or "无"}

【回顾任务】
1. 一致性检查：叙事中NPC性格/身份是否前后矛盾？事件时间线是否合理？
2. 玩家偏好分析：从玩家的行为模式推断偏好（喜欢什么类型的行为？）
3. 叙事质量评估：节奏是否合适？角色是否有深度？是否有重复/单调的段落？
4. 改进建议：下一次叙事生成应该注意什么？

【输出JSON格式】
{{
    "consistency_issues": [
        {{"issue": "描述", "severity": "low/medium/high", "suggestion": "建议"}}
    ],
    "player_preferences": [
        {{"preference": "偏好描述", "confidence": 0.0-1.0, "evidence": "证据"}}
    ],
    "quality_feedback": {{
        "consistency_score": 0.0-1.0,
        "engagement_score": 0.0-1.0,
        "pacing_score": 0.0-1.0,
        "character_depth_score": 0.0-1.0,
        "strengths": ["优点1"],
        "weaknesses": ["不足1"]
    }},
    "lessons": [
        {{"category": "player_preference/narrative_quality/world_consistency", "content": "教训内容", "importance": 0.0-1.0}}
    ]
}}

只输出JSON。"""

        try:
            result = self.llm.chat_json(prompt, temperature=0.3, max_tokens=2048)
        except Exception as e:
            logger.warning("Narrative review LLM failed: %s", e)
            return {"reviewed": False, "reason": "llm_error", "error": str(e)}

        # 处理回顾结果
        lessons_extracted = []

        # 提取教训
        for lesson_data in result.get("lessons", []):
            self._lesson_counter += 1
            lesson = Lesson(
                lesson_id=f"lesson_{self._lesson_counter}",
                category=lesson_data.get("category", "narrative_quality"),
                content=lesson_data.get("content", ""),
                importance=min(1.0, max(0.0, lesson_data.get("importance", 0.5))),
                created_turn=current_turn,
                created_day=current_day,
                evidence=[narrative_text[:200]],
            )
            # 检查是否与已有教训重复
            if not self._is_duplicate_lesson(lesson):
                self.lessons.append(lesson)
                lessons_extracted.append(lesson.to_dict())

        # 更新质量指标（指数移动平均）
        qf = result.get("quality_feedback", {})
        alpha = 0.3  # 平滑因子
        if qf:
            self.quality_metrics.consistency_score = (
                alpha * qf.get("consistency_score", 0.5) +
                (1 - alpha) * self.quality_metrics.consistency_score
            )
            self.quality_metrics.engagement_score = (
                alpha * qf.get("engagement_score", 0.5) +
                (1 - alpha) * self.quality_metrics.engagement_score
            )
            self.quality_metrics.pacing_score = (
                alpha * qf.get("pacing_score", 0.5) +
                (1 - alpha) * self.quality_metrics.pacing_score
            )
            self.quality_metrics.character_depth_score = (
                alpha * qf.get("character_depth_score", 0.5) +
                (1 - alpha) * self.quality_metrics.character_depth_score
            )
            self.quality_metrics.review_count += 1

        # 清理过期/低重要性教训
        self._cleanup_lessons(current_turn)

        review_record = {
            "turn": current_turn,
            "day": current_day,
            "lessons_count": len(lessons_extracted),
            "consistency_issues": result.get("consistency_issues", []),
            "quality_scores": self.quality_metrics.to_dict(),
        }
        self._review_history.append(review_record)
        if len(self._review_history) > 50:
            self._review_history = self._review_history[-50:]

        logger.info("Narrative review completed: %d lessons, overall=%.2f",
                     len(lessons_extracted), self.quality_metrics.overall_score)

        return {
            "reviewed": True,
            "lessons": lessons_extracted,
            "quality_metrics": self.quality_metrics.to_dict(),
            "consistency_issues": result.get("consistency_issues", []),
            "player_preferences": result.get("player_preferences", []),
            "strengths": qf.get("strengths", []),
            "weaknesses": qf.get("weaknesses", []),
        }

    def get_lessons_for_prompt(self, max_lessons: int = 5) -> str:
        """
        将教训格式化为可注入到叙事提示词中的文本。
        这是闭环学习的关键 — 教训直接影响下一次叙事生成。
        注意：此方法是只读的，不会修改 lesson 状态。
        """
        if not self.lessons:
            return ""

        # 按重要性排序，取 top N
        sorted_lessons = sorted(
            self.lessons, key=lambda l: l.importance, reverse=True
        )[:max_lessons]

        parts = []
        for lesson in sorted_lessons:
            category_label = {
                "player_preference": "玩家偏好",
                "narrative_quality": "叙事质量",
                "world_consistency": "世界一致性",
            }.get(lesson.category, lesson.category)
            parts.append(f"- [{category_label}] {lesson.content}")

        return "【叙事回顾教训】\n" + "\n".join(parts)

    def mark_lessons_applied(self, current_turn: int, max_lessons: int = 5):
        """标记 top N 教训为已应用（在叙事生成成功后调用）"""
        sorted_lessons = sorted(
            self.lessons, key=lambda l: l.importance, reverse=True
        )[:max_lessons]
        for lesson in sorted_lessons:
            lesson.times_applied += 1
            lesson.last_applied_turn = current_turn

    def get_quality_trend(self) -> dict:
        """返回质量趋势数据，用于前端展示"""
        if not self._review_history:
            return {"trend": "no_data", "history": []}

        recent_scores = []
        for record in self._review_history[-10:]:
            scores = record.get("quality_scores", {})
            recent_scores.append({
                "turn": record["turn"],
                "overall": scores.get("overall_score", 0),
                "consistency": scores.get("consistency_score", 0),
            })

        # 判断趋势
        if len(recent_scores) >= 3:
            first = recent_scores[:len(recent_scores)//2]
            second = recent_scores[len(recent_scores)//2:]
            avg_first = sum(s["overall"] for s in first) / max(1, len(first))
            avg_second = sum(s["overall"] for s in second) / max(1, len(second))
            if avg_second > avg_first * 1.05:
                trend = "improving"
            elif avg_second < avg_first * 0.95:
                trend = "declining"
            else:
                trend = "stable"
        else:
            trend = "insufficient_data"

        return {
            "trend": trend,
            "current_metrics": self.quality_metrics.to_dict(),
            "history": recent_scores,
            "total_lessons": len(self.lessons),
            "active_lessons": len([l for l in self.lessons if l.importance >= 0.3]),
        }

    def _is_duplicate_lesson(self, new_lesson: Lesson) -> bool:
        """检查是否与已有教训重复"""
        for existing in self.lessons:
            if existing.category != new_lesson.category:
                continue
            # 简单文本相似度检查
            if self._text_similarity(existing.content, new_lesson.content) > 0.7:
                # 更新重要性（取较高值）
                existing.importance = max(existing.importance, new_lesson.importance)
                return True
        return False

    @staticmethod
    def _text_similarity(a: str, b: str) -> float:
        """简单的文本相似度（基于字符集合的 Jaccard 系数）"""
        set_a = set(a)
        set_b = set(b)
        if not set_a or not set_b:
            return 0.0
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union if union > 0 else 0.0

    def _cleanup_lessons(self, current_turn: int, max_lessons: int = 30):
        """清理过期和低重要性教训"""
        # 重要性衰减：长时间未被应用的教训重要性降低
        for lesson in self.lessons:
            turns_since_applied = current_turn - lesson.last_applied_turn
            if turns_since_applied > 20 and lesson.importance > 0.1:
                lesson.importance *= 0.95

        # 移除低重要性教训
        self.lessons = [
            l for l in self.lessons
            if l.importance >= 0.15 or l.created_turn == current_turn
        ]

        # 限制总数
        if len(self.lessons) > max_lessons:
            self.lessons.sort(key=lambda l: l.importance, reverse=True)
            self.lessons = self.lessons[:max_lessons]

    def to_dict(self) -> dict:
        """序列化用于存档"""
        return {
            "lessons": [l.to_dict() for l in self.lessons],
            "quality_metrics": self.quality_metrics.to_dict(),
            "lesson_counter": self._lesson_counter,
            "last_review_turn": self._last_review_turn,
            "review_history": self._review_history[-20:],
        }

    def from_dict(self, data: dict):
        """从存档恢复"""
        self.lessons = [Lesson.from_dict(l) for l in data.get("lessons", [])]
        self._lesson_counter = data.get("lesson_counter", 0)
        self._last_review_turn = data.get("last_review_turn", 0)
        self._review_history = data.get("review_history", [])

        metrics = data.get("quality_metrics", {})
        self.quality_metrics = NarrativeQualityMetrics(
            consistency_score=metrics.get("consistency_score", 1.0),
            engagement_score=metrics.get("engagement_score", 0.5),
            pacing_score=metrics.get("pacing_score", 0.5),
            character_depth_score=metrics.get("character_depth_score", 0.5),
            review_count=metrics.get("review_count", 0),
        )
