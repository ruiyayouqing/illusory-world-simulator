"""Agent 自主记忆管理（MemGPT/Letta 式）：Agent 通过函数调用自主管理记忆。

参考 MemGPT/Letta 的设计：Agent 像操作系统管理内存一样自主管理自己的记忆，
在上下文填满前主动摘要，选择性丢弃冗余记忆，提升重要记忆为长期记忆。

与 MemoryCurator 的区别：
  - MemoryCurator：按固定间隔（每 N 回合）被动触发，规则驱动
  - AutonomousMemoryManager：每回合评估记忆状态，按上下文压力/冗余度/重要性
    主动决策，模拟 Agent 的"自我意识"记忆管理
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("chronoverse.autonomous_memory")


class MemoryAction(Enum):
    """记忆操作类型。"""
    STORE = "store"          # 存储新记忆
    RETRIEVE = "retrieve"    # 检索记忆
    SUMMARIZE = "summarize"  # 摘要压缩
    DISCARD = "discard"      # 丢弃冗余
    ARCHIVE = "archive"      # 归档（降低重要性）
    PROMOTE = "promote"      # 提升（短期→长期）


@dataclass
class MemoryDecision:
    """记忆管理决策。"""
    action: MemoryAction
    target: str  # 操作目标（记忆ID/查询/时间范围）
    reason: str  # 决策原因
    params: dict = field(default_factory=dict)
    priority: int = 0  # 优先级 0-10


class AutonomousMemoryManager:
    """
    自主记忆管理器。
    Agent 在每回合后评估记忆状态，自主决定是否执行记忆操作。
    """

    def __init__(self, memory_store=None, llm=None):
        self.memory_store = memory_store
        self.llm = llm
        self._action_history: list[MemoryDecision] = []
        self._max_history: int = 100
        self._context_pressure_threshold: int = 25000  # 上下文压力阈值（token）
        self._min_memories_to_summarize: int = 50  # 最少记忆数才考虑摘要
        self._discard_threshold: float = 0.15  # 重要性低于此值考虑丢弃

    def set_memory_store(self, memory_store):
        """注入 MemoryStore（在世界加载后由 game_engine 调用）。"""
        self.memory_store = memory_store

    def evaluate_and_act(self, entity_id: str, context_size: int, current_turn: int, current_day: int) -> list[MemoryDecision]:
        """
        评估记忆状态并执行操作。
        返回执行的操作列表。
        """
        decisions = self._plan_actions(entity_id, context_size, current_turn, current_day)
        executed = []

        for decision in decisions:
            try:
                success = self._execute_action(entity_id, decision, current_turn, current_day)
                if success:
                    executed.append(decision)
                    self._record_action(decision)
            except Exception as e:
                logger.warning("Memory action %s failed for %s: %s", decision.action.value, entity_id, e)

        if executed:
            logger.info("Autonomous memory: %d actions for %s", len(executed), entity_id)

        return executed

    def _plan_actions(self, entity_id: str, context_size: int, current_turn: int, current_day: int) -> list[MemoryDecision]:
        """规划记忆操作。"""
        decisions = []

        # 1. 上下文压力检查：如果接近上限，触发摘要
        if context_size > self._context_pressure_threshold:
            decisions.append(MemoryDecision(
                action=MemoryAction.SUMMARIZE,
                target=entity_id,
                reason=f"上下文压力高 ({context_size} tokens)",
                params={"max_memories": 20, "time_range_days": 7},
                priority=9,
            ))

        if not self.memory_store:
            return decisions

        # 2. 检查记忆数量和冗余
        try:
            all_memories = self._get_all_memories(entity_id)

            if len(all_memories) > self._min_memories_to_summarize:
                # 检查是否有低重要性记忆可丢弃
                low_importance = [m for m in all_memories if self._get_importance(m) < self._discard_threshold]
                if len(low_importance) > 10:
                    decisions.append(MemoryDecision(
                        action=MemoryAction.DISCARD,
                        target=entity_id,
                        reason=f"发现 {len(low_importance)} 条低重要性记忆",
                        params={"ids": [m.get("id", "") for m in low_importance[:10]]},
                        priority=5,
                    ))

                # 检查是否有相似记忆可合并
                similar_groups = self._find_similar_memories(all_memories)
                if similar_groups:
                    decisions.append(MemoryDecision(
                        action=MemoryAction.SUMMARIZE,
                        target=entity_id,
                        reason=f"发现 {len(similar_groups)} 组相似记忆",
                        params={"groups": similar_groups},
                        priority=6,
                    ))

            # 3. 检查是否有高重要性记忆需要提升
            high_importance = [m for m in all_memories if self._get_importance(m) > 0.8]
            for mem in high_importance:
                meta = mem.get("metadata", {})
                if meta.get("type") == "episodic":
                    decisions.append(MemoryDecision(
                        action=MemoryAction.PROMOTE,
                        target=mem.get("id", ""),
                        reason="高重要性情景记忆应提升为语义记忆",
                        params={"memory": mem},
                        priority=7,
                    ))
                    break  # 每次最多提升一个

            # 4. 检查过时记忆
            stale_memories = [m for m in all_memories
                            if current_day - self._get_day(m) > 30
                            and self._get_importance(m) < 0.5]
            if len(stale_memories) > 5:
                decisions.append(MemoryDecision(
                    action=MemoryAction.ARCHIVE,
                    target=entity_id,
                    reason=f"发现 {len(stale_memories)} 条过时记忆",
                    params={"ids": [m.get("id", "") for m in stale_memories[:5]]},
                    priority=4,
                ))

        except Exception as e:
            logger.warning("Memory planning failed for %s: %s", entity_id, e)

        # 按优先级排序
        decisions.sort(key=lambda d: d.priority, reverse=True)
        return decisions

    def _execute_action(self, entity_id: str, decision: MemoryDecision, turn: int, day: int) -> bool:
        """执行记忆操作。"""
        if decision.action == MemoryAction.SUMMARIZE:
            return self._do_summarize(entity_id, decision.params, turn, day)
        elif decision.action == MemoryAction.DISCARD:
            return self._do_discard(decision.params.get("ids", []))
        elif decision.action == MemoryAction.ARCHIVE:
            return self._do_archive(decision.params.get("ids", []))
        elif decision.action == MemoryAction.PROMOTE:
            return self._do_promote(decision.params.get("memory", {}), entity_id, turn, day)
        return False

    def _do_summarize(self, entity_id: str, params: dict, turn: int, day: int) -> bool:
        """执行摘要压缩。"""
        if not self.llm or not self.memory_store:
            return False

        groups = params.get("groups")
        if groups:
            # 合并相似记忆组
            for group in groups:
                if len(group) < 2:
                    continue
                texts = [m.get("text", "") for m in group]
                try:
                    summary = self._llm_summarize(texts)
                    if summary:
                        # [Bug#19] 先存储摘要，成功后再删除原记忆，避免异常时数据丢失
                        self.memory_store.add_memory_with_importance(
                            text=summary,
                            importance=0.7,
                            memory_type="semantic",
                            metadata={"type": "summary", "turn": turn, "day": day}
                        )
                        for m in group:
                            self._delete_memory(m.get("id", ""))
                except Exception as e:
                    logger.warning("Summarize group failed: %s", e)
            return True
        else:
            # 按时间范围摘要
            max_memories = params.get("max_memories", 20)
            time_range = params.get("time_range_days", 7)
            memories = self._get_recent_memories(entity_id, time_range, max_memories)
            if len(memories) < 3:
                return False
            texts = [m.get("text", "") for m in memories]
            try:
                summary = self._llm_summarize(texts)
                if summary:
                    # [Bug#19] 先存储摘要，成功后再删除原记忆
                    self.memory_store.add_memory_with_importance(
                        text=summary,
                        importance=0.7,
                        memory_type="semantic",
                        metadata={"type": "summary", "turn": turn, "day": day}
                    )
                    for m in memories:
                        self._delete_memory(m.get("id", ""))
                    return True
            except Exception as e:
                logger.warning("Summarize failed: %s", e)
        return False

    def _do_discard(self, ids: list[str]) -> bool:
        """丢弃低重要性记忆。"""
        for mid in ids:
            self._delete_memory(mid)
        return True

    def _do_archive(self, ids: list[str]) -> bool:
        """归档过时记忆（降低重要性）。"""
        if not self.memory_store:
            return False
        for mid in ids:
            try:
                # ChromaDB 更新 metadata
                self.memory_store.collection.update(
                    ids=[mid],
                    metadatas=[{"importance": 0.1, "archived": True}]
                )
            except Exception as e:
                logger.warning("Archive memory %s failed: %s", mid, e)
        return True

    def _do_promote(self, memory: dict, entity_id: str, turn: int, day: int) -> bool:
        """将高重要性情景记忆提升为语义记忆。"""
        if not self.memory_store:
            return False
        text = memory.get("text", "")
        if not text:
            return False
        try:
            self.memory_store.add_memory_with_importance(
                text=text,
                importance=self._get_importance(memory),
                memory_type="semantic",
                metadata={"type": "promoted", "turn": turn, "day": day}
            )
            # [Bug#20] 提升成功后删除原始情景记忆，避免同一信息存在两份
            self._delete_memory(memory.get("id", ""))
            return True
        except Exception as e:
            logger.warning("Promote memory failed: %s", e)
            return False

    def _llm_summarize(self, texts: list[str]) -> str:
        """用 LLM 摘要多条记忆。"""
        combined = "\n".join(f"- {t}" for t in texts)
        prompt = f"""请将以下记忆条目合并为一条简洁的摘要，保留关键信息：

{combined}

摘要："""
        try:
            result = self.llm.chat(prompt, temperature=0.3, max_tokens=1024)
            return result.strip() if result else ""
        except Exception as e:
            logger.warning("LLM summarize failed: %s", e)
            return ""

    def _get_all_memories(self, entity_id: str) -> list[dict]:
        """获取实体所有记忆。"""
        if not self.memory_store:
            return []
        try:
            # [Bug#8] 使用 where 条件过滤 entity_id
            results = self.memory_store.collection.get(
                where={"entity_id": entity_id} if entity_id else None
            )
            docs = results.get("documents", [])
            metas = results.get("metadatas", [])
            ids = results.get("ids", [])
            return [{"id": ids[i], "text": docs[i], "metadata": metas[i]}
                    for i in range(len(docs))]
        except Exception:
            return []

    def _get_recent_memories(self, entity_id: str, days: int, limit: int) -> list[dict]:
        """获取近期记忆。"""
        all_mem = self._get_all_memories(entity_id)
        # [Bug#7] 使用 days 参数过滤，而非恒为 True 的条件
        if days > 0:
            # 获取当前游戏天数（从最新记忆推断，或使用元数据中的最大值）
            max_day = max((m.get("metadata", {}).get("day", 0) for m in all_mem), default=0)
            cutoff_day = max(0, max_day - days)
            recent = [m for m in all_mem if m.get("metadata", {}).get("day", 0) >= cutoff_day]
        else:
            recent = all_mem
        return recent[:limit]

    def _find_similar_memories(self, memories: list[dict], threshold: float = 0.7) -> list[list[dict]]:
        """查找相似记忆组。"""
        groups = []
        used = set()

        for i, m1 in enumerate(memories):
            if i in used:
                continue
            group = [m1]
            used.add(i)
            text1 = m1.get("text", "")

            for j, m2 in enumerate(memories[i+1:], i+1):
                if j in used:
                    continue
                text2 = m2.get("text", "")
                # 简单字符级相似度
                sim = self._text_similarity(text1, text2)
                if sim > threshold:
                    group.append(m2)
                    used.add(j)

            if len(group) > 1:
                groups.append(group)

        return groups

    def _text_similarity(self, a: str, b: str) -> float:
        """字符级 Jaccard 相似度。"""
        if not a or not b:
            return 0.0
        set_a = set(a)
        set_b = set(b)
        return len(set_a & set_b) / len(set_a | set_b) if (set_a | set_b) else 0.0

    def _get_importance(self, memory: dict) -> float:
        """获取记忆重要性。"""
        return float(memory.get("metadata", {}).get("importance", 0.5))

    def _get_day(self, memory: dict) -> int:
        """获取记忆天数。"""
        return int(memory.get("metadata", {}).get("day", 0))

    def _delete_memory(self, mem_id: str):
        """删除记忆。"""
        if not self.memory_store or not mem_id:
            return
        try:
            self.memory_store.collection.delete(ids=[mem_id])
        except Exception as e:
            logger.warning("Delete memory %s failed: %s", mem_id, e)

    def _record_action(self, decision: MemoryDecision):
        """记录操作历史。"""
        self._action_history.append(decision)
        if len(self._action_history) > self._max_history:
            self._action_history = self._action_history[-self._max_history:]

    def get_stats(self) -> dict:
        """获取统计信息。"""
        action_counts: dict[str, int] = {}
        for d in self._action_history:
            action_counts[d.action.value] = action_counts.get(d.action.value, 0) + 1
        return {
            "total_actions": len(self._action_history),
            "action_counts": action_counts,
            "context_pressure_threshold": self._context_pressure_threshold,
        }

    def to_dict(self) -> dict:
        return {
            "action_history": [
                {"action": d.action.value, "target": d.target, "reason": d.reason, "priority": d.priority}
                for d in self._action_history[-50:]  # 只保留最近50条
            ],
        }

    def from_dict(self, data: dict):
        history = []
        for item in data.get("action_history", []):
            try:
                action = MemoryAction(item.get("action", "store"))
                history.append(MemoryDecision(
                    action=action,
                    target=item.get("target", ""),
                    reason=item.get("reason", ""),
                    priority=item.get("priority", 0),
                ))
            except Exception:
                continue
        self._action_history = history
