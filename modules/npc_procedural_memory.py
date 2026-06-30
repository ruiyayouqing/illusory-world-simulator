"""
[v10] NPC 程序性记忆 — 让 NPC 从经验中学习

核心思路（借鉴 Hermes Agent 的"技能即程序性记忆"）：
  1. NPC 执行动作后，记录"动作-上下文-结果"三元组
  2. 计算动作有效性评分
  3. 规划时检索相关程序性记忆，优先选择历史上有效的动作
  4. 定期整合：合并重复记忆，衰减过时记忆

设计原则：
  - 每个 NPC 有独立的程序性记忆空间
  - 记忆有容量上限，按重要性淘汰
  - 与 BranchPlanner 配合：规划时参考历史经验
"""
from __future__ import annotations
import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schemas import NPCState, WorldState

logger = logging.getLogger("chronoverse.npc_procedural")


@dataclass
class ProceduralEntry:
    """一条 NPC 程序性记忆"""
    action_type: str          # 动作类型（work/social/explore/trade/combat/idle）
    context: str              # 触发上下文描述
    outcome: str              # 结果描述
    effectiveness: float      # 有效性评分 0.0-1.0
    energy_cost: int          # 消耗的精力
    day: int                  # 发生的天数
    location: str             # 发生的地点
    times_recalled: int = 0   # 被回忆的次数
    success_count: int = 0    # 成功次数
    failure_count: int = 0    # 失败次数
    recency_weight: float = 1.0  # 时间衰减权重（检索时综合使用，不修改原始 effectiveness）

    def to_dict(self) -> dict:
        return {
            "action_type": self.action_type,
            "context": self.context,
            "outcome": self.outcome,
            "effectiveness": round(self.effectiveness, 3),
            "energy_cost": self.energy_cost,
            "day": self.day,
            "location": self.location,
            "times_recalled": self.times_recalled,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "recency_weight": round(self.recency_weight, 3),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProceduralEntry":
        from .schemas import safe_dataclass_from_dict
        return safe_dataclass_from_dict(cls, d)


class NPCProceduralMemory:
    """
    [v10] NPC 程序性记忆管理器

    每个 NPC 拥有独立的程序性记忆空间，记录"什么动作在什么情况下有效"。
    与 BranchPlanner 配合，为 NPC 的决策提供历史经验参考。
    """

    def __init__(self, max_entries_per_npc: int = 30):
        # npc_agent_id -> list[ProceduralEntry]
        self._memories: dict[str, list[ProceduralEntry]] = {}
        self.max_entries_per_npc = max_entries_per_npc

    def record_action(self, npc: "NPCState", action_type: str,
                      context: str, outcome: str,
                      effectiveness: float, energy_cost: int,
                      day: int, location: str = ""):
        """
        记录一次 NPC 动作经验。

        Args:
            npc: NPC状态
            action_type: 动作类型
            context: 触发上下文
            outcome: 结果描述
            effectiveness: 有效性 0-1
            energy_cost: 精力消耗
            day: 天数
            location: 地点
        """
        npc_id = npc.agent_id
        if npc_id not in self._memories:
            self._memories[npc_id] = []

        entry = ProceduralEntry(
            action_type=action_type,
            context=context[:200],
            outcome=outcome[:200],
            effectiveness=min(1.0, max(0.0, effectiveness)),
            energy_cost=energy_cost,
            day=day,
            location=location or npc.current_location or "",
        )

        # 检查是否有高度相似的记忆，合并而非新增
        merged = False
        for existing in self._memories[npc_id]:
            if (existing.action_type == action_type and
                    existing.location == entry.location and
                    self._context_similarity(existing.context, context) > 0.6):
                # 合并：更新有效性（指数移动平均）
                alpha = 0.3
                clamped = min(1.0, max(0.0, effectiveness))
                existing.effectiveness = (
                    alpha * clamped + (1 - alpha) * existing.effectiveness
                )
                existing.effectiveness = min(1.0, max(0.0, existing.effectiveness))
                if effectiveness >= 0.6:
                    existing.success_count += 1
                else:
                    existing.failure_count += 1
                existing.day = day  # 更新为最新时间
                existing.outcome = outcome[:200]
                merged = True
                break

        if not merged:
            if effectiveness >= 0.6:
                entry.success_count = 1
            else:
                entry.failure_count = 1
            self._memories[npc_id].append(entry)

        # 容量控制
        self._trim_memories(npc_id)

    def get_action_suggestions(self, npc: "NPCState",
                               world_state: "WorldState",
                               context: str = "") -> list[dict]:
        """
        根据程序性记忆，为 NPC 规划提供建议。

        Returns:
            建议列表，每项包含 action_type, effectiveness, reason
        """
        npc_id = npc.agent_id
        if npc_id not in self._memories or not self._memories[npc_id]:
            return []

        current_location = npc.current_location or ""
        suggestions = []

        # 按动作类型分组统计
        action_stats: dict[str, dict] = {}
        for entry in self._memories[npc_id]:
            at = entry.action_type
            if at not in action_stats:
                action_stats[at] = {
                    "total_effectiveness": 0.0,
                    "count": 0,
                    "total_energy": 0,
                    "best_location": "",
                    "best_effectiveness": 0.0,
                }
            stats = action_stats[at]
            stats["total_effectiveness"] += entry.effectiveness * entry.recency_weight
            stats["count"] += 1
            stats["total_energy"] += entry.energy_cost
            # 记录最佳地点
            combined = entry.effectiveness * entry.recency_weight
            if combined > stats["best_effectiveness"]:
                stats["best_effectiveness"] = combined
                stats["best_location"] = entry.location

        for action_type, stats in action_stats.items():
            avg_effectiveness = stats["total_effectiveness"] / stats["count"]
            avg_energy = stats["total_energy"] / stats["count"]

            # 位置匹配加分
            location_bonus = 0.0
            if stats["best_location"] == current_location:
                location_bonus = 0.15

            adjusted_score = min(1.0, avg_effectiveness + location_bonus)

            reason = f"历史经验：{stats['count']}次尝试，平均有效性{avg_effectiveness:.1%}"
            if location_bonus > 0:
                reason += f"，当前位置有{location_bonus:.0%}加成"

            suggestions.append({
                "action_type": action_type,
                "effectiveness": round(adjusted_score, 3),
                "avg_energy_cost": round(avg_energy),
                "sample_count": stats["count"],
                "reason": reason,
                "best_location": stats["best_location"],
            })

        # 按有效性排序
        suggestions.sort(key=lambda s: s["effectiveness"], reverse=True)
        return suggestions[:5]

    def get_npc_experience_summary(self, npc_id: str) -> str:
        """生成 NPC 经验摘要，用于注入到 LLM prompt"""
        if npc_id not in self._memories or not self._memories[npc_id]:
            return ""

        entries = self._memories[npc_id]
        if not entries:
            return ""

        # 按动作类型分组
        by_type: dict[str, list[ProceduralEntry]] = {}
        for entry in entries:
            by_type.setdefault(entry.action_type, []).append(entry)

        parts = []
        for action_type, type_entries in by_type.items():
            avg_eff = sum(e.effectiveness for e in type_entries) / len(type_entries)
            successes = sum(1 for e in type_entries if e.effectiveness >= 0.6)
            parts.append(
                f"- {action_type}: {len(type_entries)}次经验，"
                f"成功率{successes}/{len(type_entries)}，"
                f"平均有效性{avg_eff:.0%}"
            )

        return "【程序性记忆】\n" + "\n".join(parts)

    def evolve_memories(self, current_day: int):
        """
        定期演化：衰减旧记忆，整合重复记忆。
        建议每 10 天调用一次。
        """
        for npc_id in list(self._memories.keys()):
            entries = self._memories[npc_id]
            if not entries:
                continue

            # 时间衰减：超过 30 天未更新的记忆重要性降低
            # 用独立字段 recency_weight 记录衰减，不修改原始 effectiveness
            for entry in entries:
                age = current_day - entry.day
                if age > 30:
                    decay = math.exp(-0.02 * (age - 30))
                    entry.recency_weight *= decay

            # 移除综合评分极低的记忆
            self._memories[npc_id] = [
                e for e in entries if e.effectiveness * e.recency_weight >= 0.1
            ]

            self._trim_memories(npc_id)

    def _trim_memories(self, npc_id: str):
        """裁剪到容量上限，保留高有效性的记忆"""
        if len(self._memories[npc_id]) > self.max_entries_per_npc:
            self._memories[npc_id].sort(
                key=lambda e: e.effectiveness * e.recency_weight, reverse=True
            )
            self._memories[npc_id] = self._memories[npc_id][:self.max_entries_per_npc]

    @staticmethod
    def _context_similarity(a: str, b: str) -> float:
        """简单的上下文相似度"""
        if not a or not b:
            return 0.0
        words_a = set(a.split())
        words_b = set(b.split())
        if not words_a or not words_b:
            return 0.0
        intersection = len(words_a & words_b)
        union = len(words_a | words_b)
        return intersection / union if union > 0 else 0.0

    def to_dict(self) -> dict:
        """序列化用于存档"""
        return {
            npc_id: [e.to_dict() for e in entries]
            for npc_id, entries in self._memories.items()
        }

    def from_dict(self, data: dict):
        """从存档恢复"""
        self._memories = {}
        for npc_id, entries_data in data.items():
            self._memories[npc_id] = [
                ProceduralEntry.from_dict(e) for e in entries_data
            ]

    def get_stats(self) -> dict:
        """返回统计信息"""
        total_entries = sum(len(e) for e in self._memories.values())
        return {
            "total_npcs": len(self._memories),
            "total_entries": total_entries,
            "avg_entries_per_npc": (
                round(total_entries / len(self._memories), 1)
                if self._memories else 0
            ),
        }
