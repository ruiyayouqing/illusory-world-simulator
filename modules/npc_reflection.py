"""NPC 反思机制（Generative Agents 式）：定期从 episodic 记忆生成高层洞察。

参考斯坦福 Generative Agents：NPC 在空闲时段（如夜间）遍历当天/近期 episodic 记忆，
由 LLM 生成抽象洞察（如"周宁冒险救我→我应更信任他"），写入 semantic 记忆。
这些洞察比原始事件更高层，影响后续决策，使 NPC 行为更可信、有成长弧。

设计要点：
  - 每 N 天反思一次（默认 3 天），避免过多 LLM 调用
  - 从近期记忆中随机选取焦点记忆，由 LLM 生成 2-3 条洞察
  - 洞察写入 semantic 记忆（带重要性评分），并缓存到内存供 prompt 注入
  - 失败时不影响主流程（向后兼容）
"""
from __future__ import annotations
import logging
import random
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .llm.base_llm import BaseLLM
    from .db.chroma_db import MemoryStore

logger = logging.getLogger("chronoverse.reflection")


@dataclass
class Insight:
    """反思洞察。"""
    content: str  # 洞察内容
    source_memories: list[str]  # 来源记忆 ID 列表
    importance: float  # 重要性 0-1
    turn: int  # 生成时的回合
    day: int  # 生成时的天数
    tags: list[str] = field(default_factory=list)  # 标签（如"社交""生存"）

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "source_memories": self.source_memories,
            "importance": self.importance,
            "turn": self.turn,
            "day": self.day,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Insight":
        # 容错：缺失字段时使用默认值，避免旧存档加载失败
        return cls(
            content=data.get("content", ""),
            source_memories=data.get("source_memories", []),
            importance=float(data.get("importance", 0.5)),
            turn=int(data.get("turn", 0)),
            day=int(data.get("day", 0)),
            tags=data.get("tags", []),
        )


class NPCReflection:
    """NPC 反思管理器。"""

    def __init__(self, llm: "BaseLLM" = None, memory_store: "MemoryStore" = None):
        self.llm = llm
        self.memory_store = memory_store
        self._insights: dict[str, list[Insight]] = {}  # npc_id -> insights
        self._last_reflection_day: dict[str, int] = {}  # npc_id -> last day
        self._reflection_interval: int = 3  # 每3天反思一次
        self._max_insights_per_npc: int = 20

    def set_memory_store(self, memory_store: "MemoryStore"):
        """延迟注入 MemoryStore（在 world 加载后调用）。"""
        self.memory_store = memory_store

    def should_reflect(self, npc_id: str, current_day: int) -> bool:
        """检查 NPC 是否应该反思。"""
        last_day = self._last_reflection_day.get(npc_id, 0)
        return current_day - last_day >= self._reflection_interval

    def reflect(self, npc_id: str, npc_name: str, current_turn: int,
                current_day: int) -> list[Insight]:
        """
        执行反思：从近期 episodic 记忆生成高层洞察。
        失败时返回空列表，不影响主流程。
        """
        if not self.llm or not self.memory_store:
            return []

        if not self.should_reflect(npc_id, current_day):
            return []

        try:
            # 1. 检索 NPC 近期记忆（以 NPC 名字为查询，召回相关叙事）
            recent_memories = self._get_recent_memories(npc_id, npc_name, current_day)
            if len(recent_memories) < 3:
                logger.debug("NPC %s 记忆不足，无法反思 (%d 条)", npc_name, len(recent_memories))
                return []

            # 2. 选择反思焦点（随机3-5条记忆）
            focus_memories = random.sample(recent_memories, min(5, len(recent_memories)))

            # 3. LLM 生成洞察
            insights = self._generate_insights(npc_name, focus_memories, current_turn, current_day)

            # 4. 缓存洞察到内存
            if npc_id not in self._insights:
                self._insights[npc_id] = []
            self._insights[npc_id].extend(insights)

            # 限制数量：按重要性排序，保留最重要的
            if len(self._insights[npc_id]) > self._max_insights_per_npc:
                self._insights[npc_id].sort(key=lambda x: x.importance, reverse=True)
                self._insights[npc_id] = self._insights[npc_id][:self._max_insights_per_npc]

            # 5. 将洞察写入 semantic 记忆（使用 v10 带重要性的存储）
            for insight in insights:
                try:
                    self.memory_store.add_memory_with_importance(
                        text=f"[{npc_name}的反思] {insight.content}",
                        metadata={
                            "type": "reflection",
                            "memory_type": "semantic",
                            "npc_id": npc_id,
                            "npc_name": npc_name,
                            "turn": current_turn,
                            "day": current_day,
                            "tags": ",".join(insight.tags) if insight.tags else "",
                        },
                        importance=insight.importance,
                        emotional_weight=0.0,
                        memory_type="semantic",
                    )
                except Exception as e:
                    logger.warning("存储 NPC %s 的洞察失败: %s", npc_name, e)

            self._last_reflection_day[npc_id] = current_day
            logger.info("NPC %s 完成反思：生成 %d 条洞察", npc_name, len(insights))

            return insights
        except Exception as e:
            logger.warning("NPC %s 反思失败: %s", npc_name, e, exc_info=True)
            return []

    def _get_recent_memories(self, npc_id: str, npc_name: str,
                             current_day: int, days: int = 3) -> list[dict]:
        """
        获取 NPC 近期 episodic 记忆。
        以 NPC 名字为查询检索相关叙事，再按天数过滤。
        """
        if not self.memory_store:
            return []
        try:
            # 优先使用带重要性排序的检索（v10）
            if hasattr(self.memory_store, "search_memory_ranked"):
                results = self.memory_store.search_memory_ranked(
                    query=npc_name, n_results=20, current_turn=current_day
                )
            else:
                results = self.memory_store.search_memory(npc_name, n_results=20)

            # 过滤近 days 天的记忆，并排除已有的反思洞察（避免自引用）
            recent = []
            for r in results:
                meta = r.get("metadata", {}) or {}
                # 跳过反思洞察本身，只反思原始事件
                if meta.get("type") == "reflection":
                    continue
                mem_day = meta.get("day", meta.get("created_day", 0))
                try:
                    mem_day = int(mem_day)
                except (TypeError, ValueError):
                    mem_day = 0
                if 0 < current_day - mem_day <= days:
                    recent.append(r)
            return recent
        except Exception as e:
            logger.warning("获取 NPC %s 近期记忆失败: %s", npc_id, e)
            return []

    def _generate_insights(self, npc_name: str, memories: list[dict],
                           turn: int, day: int) -> list[Insight]:
        """用 LLM 从记忆生成洞察。"""
        memory_texts = []
        for i, m in enumerate(memories):
            text = m.get("text", m.get("document", ""))
            if not text:
                continue
            memory_texts.append(f"{i + 1}. {text[:300]}")

        if not memory_texts:
            return []

        memories_str = "\n".join(memory_texts)

        prompt = f"""你是角色"{npc_name}"的内心反思系统。请基于以下近期记忆，生成2-3条高层洞察。

【近期记忆】
{memories_str}

【要求】
1. 洞察应是对记忆的抽象总结，而非简单复述
2. 洞察应能指导未来行为（如"我应该更信任某人""某地危险应避开"）
3. 洞察应反映角色的成长和认知变化
4. 用第一人称（"我"）表述

返回 JSON：
{{
    "insights": [
        {{
            "content": "洞察内容（第一人称）",
            "importance": 0.0-1.0,
            "tags": ["社交", "生存", "职业", "探索"]
        }}
    ]
}}"""

        try:
            # chat_json 更灵活，支持自定义 JSON 结构
            result = self.llm.chat_json(prompt, temperature=0.5)
            if not result or "error" in result:
                return []

            insights_data = result.get("insights", [])
            if not isinstance(insights_data, list):
                return []

            insights = []
            for item in insights_data:
                if not isinstance(item, dict):
                    continue
                content = item.get("content", "")
                if not content:
                    continue
                try:
                    importance = float(item.get("importance", 0.5))
                except (TypeError, ValueError):
                    importance = 0.5
                insight = Insight(
                    content=content,
                    source_memories=[m.get("id", "") for m in memories],
                    importance=min(1.0, max(0.0, importance)),
                    turn=turn,
                    day=day,
                    tags=item.get("tags", []) if isinstance(item.get("tags"), list) else [],
                )
                insights.append(insight)

            return insights
        except Exception as e:
            logger.warning("NPC %s 洞察生成失败: %s", npc_name, e)
            return []

    def get_insights_for_prompt(self, npc_id: str, top_k: int = 3) -> str:
        """获取 NPC 的洞察，用于注入 prompt。"""
        insights = self._insights.get(npc_id, [])
        if not insights:
            return ""

        # 按重要性排序，取前 top_k 条
        sorted_insights = sorted(insights, key=lambda x: x.importance, reverse=True)
        top = sorted_insights[:top_k]

        parts = [f"- {ins.content}" for ins in top]
        return "【近期反思】\n" + "\n".join(parts)

    def batch_reflect(self, npc_states: dict, current_turn: int,
                      current_day: int, max_npcs: int = 10) -> dict:
        """
        批量反思：对多个 NPC 执行反思。
        只对应该反思的 NPC 执行，限制数量避免过多 LLM 调用。
        npc_states: dict[npc_id, NPCState]
        """
        results = {}
        reflected_count = 0

        for npc_id, npc in npc_states.items():
            if reflected_count >= max_npcs:
                break
            if not self.should_reflect(npc_id, current_day):
                continue

            try:
                insights = self.reflect(npc_id, npc.name, current_turn, current_day)
                if insights:
                    results[npc_id] = len(insights)
                    reflected_count += 1
            except Exception as e:
                logger.warning("NPC %s 批量反思失败: %s", npc_id, e)

        if results:
            logger.info("批量反思完成：%d 个 NPC 生成洞察", len(results))

        return results

    def to_dict(self) -> dict:
        """序列化用于存档。"""
        return {
            "insights": {
                nid: [i.to_dict() for i in ins]
                for nid, ins in self._insights.items()
            },
            "last_reflection_day": dict(self._last_reflection_day),
        }

    def from_dict(self, data: dict):
        """从存档恢复。"""
        self._insights = {
            nid: [Insight.from_dict(i) for i in ins]
            for nid, ins in data.get("insights", {}).items()
        }
        self._last_reflection_day = {
            nid: int(d) for nid, d in data.get("last_reflection_day", {}).items()
        }

    def get_stats(self) -> dict:
        """返回统计信息。"""
        total_insights = sum(len(ins) for ins in self._insights.values())
        return {
            "npcs_with_insights": len(self._insights),
            "total_insights": total_insights,
            "reflection_interval": self._reflection_interval,
        }
