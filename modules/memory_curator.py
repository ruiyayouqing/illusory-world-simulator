"""
[v10] 记忆 Curator — 世界记忆维护系统

核心思路（借鉴 Hermes Agent 的 Curator 后台维护机制）：
  1. 定期（每 N 回合）触发"世界记忆整理"
  2. 合并冗余的 ChromaDB 条目
  3. 归档不再相关的记忆（已死亡 NPC 的日常行为等）
  4. 提取跨叙事的模式（玩家行为偏好、世界演化趋势）
  5. 更新世界观 lorebook 条目

设计原则：
  - Curator 永不删除记忆，只归档和整合
  - 操作可逆（归档区可恢复）
  - 低频运行，不影响游戏性能
"""
from __future__ import annotations
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .llm.base_llm import BaseLLM
    from .db.chroma_db import MemoryStore
    from .schemas import WorldState, PlayerState, NPCState
    from .lorebook import Lorebook

logger = logging.getLogger("chronoverse.curator")


class MemoryCurator:
    """
    [v10] 记忆 Curator — 世界记忆维护

    定期整理 ChromaDB 中的记忆条目：
    - 合并冗余条目
    - 归档过时条目
    - 提取跨叙事模式
    - 更新 lorebook
    """

    def __init__(self, llm: "BaseLLM", curate_interval: int = 15, summary_interval: int = 10):
        self.llm = llm
        self.curate_interval = curate_interval
        self.summary_interval = summary_interval
        self._last_curate_turn = 0
        self._last_summary_turn = 0
        self._curate_history: list[dict] = []
        self._archived_memories: list[dict] = []  # 归档区
        self._history_summaries: list[dict] = []  # 历史摘要
        self._summary_counter = 0

    def should_curate(self, current_turn: int) -> bool:
        """判断是否应该触发整理"""
        return current_turn - self._last_curate_turn >= self.curate_interval

    def should_summarize(self, current_turn: int) -> bool:
        """判断是否应该触发历史摘要"""
        return current_turn - self._last_summary_turn >= self.summary_interval and current_turn > 0

    def summarize_history(self, narrative_history: list, current_turn: int, current_day: int) -> dict:
        """
        滚动摘要：将最早的 summary_interval 条叙事压缩成摘要
        返回摘要结果，并更新历史列表
        """
        if len(narrative_history) < self.summary_interval + 5:
            return {"status": "skipped", "reason": "history too short"}

        self._last_summary_turn = current_turn
        self._summary_counter += 1

        to_summarize = narrative_history[:self.summary_interval]
        remaining = narrative_history[self.summary_interval:]

        summary_text = self._generate_summary(to_summarize, self._summary_counter)

        summary_entry = {
            "type": "summary",
            "summary_id": f"summary_{self._summary_counter}",
            "day_range": [to_summarize[0].get("day", 0) if to_summarize else 0,
                         to_summarize[-1].get("day", current_day) if to_summarize else current_day],
            "turn_range": [to_summarize[0].get("turn", 0) if to_summarize else 0,
                         to_summarize[-1].get("turn", current_turn) if to_summarize else current_turn],
            "text": summary_text,
            "entry_count": len(to_summarize),
            "key_npcs": self._extract_key_entities(to_summarize),
            "key_events": self._extract_key_events(to_summarize),
        }

        self._history_summaries.append(summary_entry)

        logger.info("History summary #%d created: %d entries compressed",
                     self._summary_counter, len(to_summarize))

        return {
            "status": "success",
            "summary": summary_entry,
            "summarized_count": len(to_summarize),
            "remaining": remaining,
            "replacement": [summary_entry]
        }

    def _generate_summary(self, entries: list, summary_num: int) -> str:
        """使用LLM生成摘要"""
        if not entries:
            return ""

        content_parts = []
        for e in entries:
            if e.get("type") == "narrative":
                content_parts.append(f"第{e.get('day', '?')}天：{e.get('text', '')[:500]}")
            elif e.get("type") == "event":
                content_parts.append(f"【事件】{e.get('text', '')[:300]}")
            elif e.get("type") == "summary":
                content_parts.append(f"【前期摘要】{e.get('text', '')}")

        full_text = "\n".join(content_parts[-15:])

        prompt = f"""你是一个游戏历史记录官，请将以下游戏叙事片段压缩成一段简洁的剧情摘要。

【要求】
1. 保留关键剧情进展、重要NPC出场、重大事件
2. 忽略无关紧要的细节、重复的日常描写
3. 字数控制在500-1000字
4. 用第三人称叙述，保持故事连贯性
5. 如果有玩家的重要选择，一定要提及

【待摘要内容】
{full_text[:3000]}

【摘要】"""

        try:
            result = self.llm.chat(prompt, temperature=0.4, max_tokens=1024)
            return result.strip()
        except Exception as e:
            logger.warning("Summary generation failed, using fallback: %s", e)
            return self._fallback_summary(entries)

    def _fallback_summary(self, entries: list) -> str:
        """LLM失败时的简单后备摘要"""
        if not entries:
            return ""
        days = set()
        npcs_mentioned = set()
        events = []
        for e in entries:
            if e.get("day"):
                days.add(e["day"])
            text = e.get("text", "")
            if e.get("type") == "event":
                events.append(text[:100])
        day_str = f"第{min(days)}天至第{max(days)}天" if days else "一段时间内"
        event_str = "；".join(events[:3]) if events else "发生了一些故事"
        return f"{day_str}，{event_str}。（注：这是简单摘要，详细记录已归档）"

    def _extract_key_entities(self, entries: list) -> list[str]:
        """[Bug] 简单提取提及的NPC名字（后备方案，不用LLM）。
        之前直接 return []，导致摘要的 key_npcs 字段始终为空。"""
        import re
        # 从叙事文本中提取常见的中文人名模式（2-4字连续中文，后接称谓）
        name_pattern = re.compile(r'([\u4e00-\u9fff]{2,4})(?:道|说|笑|怒|惊|叹|问|答|看|想|走|来|去)')
        names = set()
        for e in entries:
            text = e.get("text", "")
            for m in name_pattern.finditer(text):
                names.add(m.group(1))
        return list(names)[:10]

    def _extract_key_events(self, entries: list) -> list[str]:
        """[Bug] 提取关键事件——之前只收 type=="event"，漏掉 type=="narrative" 的关键剧情。"""
        events = []
        for e in entries:
            etype = e.get("type", "")
            text = e.get("text", "")
            if not text:
                continue
            if etype == "event":
                events.append(text[:100])
            elif etype == "narrative":
                # 从 narrative 中提取包含关键动词的句子作为事件
                import re
                # 匹配包含关键剧情动词的句子
                key_verbs = ["死", "生", "战", "胜", "败", "逃", "获", "失", "婚", "病", "伤", "悟", "破", "立", "封", "赐", "罚"]
                sentences = re.split(r'[。！？\n]', text)
                for s in sentences:
                    s = s.strip()
                    if len(s) >= 4 and any(v in s for v in key_verbs):
                        events.append(s[:100])
                        if len(events) >= 5:
                            break
            if len(events) >= 5:
                break
        return events[:5]

    def get_context_for_llm(self, narrative_history: list, recent_window: int = 10) -> str:
        """
        获取用于LLM上下文的记忆：
        - 所有历史摘要（按时间顺序）
        - 最近 recent_window 条完整叙事
        """
        parts = []

        if self._history_summaries:
            parts.append("【前期剧情摘要】")
            for s in self._history_summaries[-5:]:
                parts.append(s["text"])
            parts.append("")

        recent = narrative_history[-recent_window:] if narrative_history else []
        if recent:
            parts.append(f"【最近{len(recent)}条剧情】")
            for e in recent:
                if e.get("type") == "summary":
                    parts.append(e.get("text", ""))
                elif e.get("text"):
                    day = e.get("day", "")
                    prefix = f"第{day}天：" if day else ""
                    parts.append(f"{prefix}{e.get('text', '')[:800]}")

        return "\n".join(parts)

    def get_summary_count(self) -> int:
        return len(self._history_summaries)

    def get_all_summaries(self) -> list[dict]:
        return self._history_summaries.copy()

    def curate(self, memory: "MemoryStore",
               player_state: "PlayerState",
               world_state: "WorldState",
               npc_states: dict[str, "NPCState"],
               lorebook: "Lorebook" = None,
               current_turn: int = 0,
               current_day: int = 0) -> dict:
        """
        执行一次记忆整理。

        Returns:
            整理结果报告
        """
        self._last_curate_turn = current_turn
        report = {
            "turn": current_turn,
            "day": current_day,
            "actions": [],
            "merged_count": 0,
            "archived_count": 0,
            "patterns_extracted": 0,
        }

        # Step 1: 整理主记忆（合并冗余）
        merge_result = self._merge_redundant_memories(memory)
        report["merged_count"] = merge_result
        if merge_result > 0:
            report["actions"].append(f"合并了 {merge_result} 条冗余记忆")

        # Step 2: 归档过时记忆
        archive_result = self._archive_outdated_memories(
            memory, npc_states, current_day
        )
        report["archived_count"] = archive_result
        if archive_result > 0:
            report["actions"].append(f"归档了 {archive_result} 条过时记忆")

        # Step 3: 提取玩家行为模式
        patterns = self._extract_player_patterns(memory, player_state)
        report["patterns_extracted"] = len(patterns)
        if patterns:
            report["actions"].append(f"提取了 {len(patterns)} 条行为模式")
            report["patterns"] = patterns

        # Step 4: 更新 lorebook
        if lorebook:
            lorebook_updates = self._update_lorebook(
                lorebook, player_state, world_state, npc_states, current_day
            )
            if lorebook_updates:
                report["actions"].append(f"更新了 {len(lorebook_updates)} 条 lorebook 条目")
                report["lorebook_updates"] = lorebook_updates

        self._curate_history.append(report)
        if len(self._curate_history) > 20:
            self._curate_history = self._curate_history[-20:]

        logger.info("Curate completed: %s", "; ".join(report["actions"]) if report["actions"] else "no changes")
        return report

    def _merge_redundant_memories(self, memory: "MemoryStore") -> int:
        """合并冗余的记忆条目（优化：限制处理数量，先用哈希去重再做文本相似度）"""
        try:
            # 获取所有记忆（限制最多处理 200 条，避免 O(N²) 在大数据量下卡死）
            all_memories = memory.collection.get()
            if not all_memories or not all_memories["ids"]:
                return 0

            documents = all_memories["documents"]
            ids = all_memories["ids"]

            # 如果数量过多，只取最近的 200 条
            if len(documents) > 200:
                documents = documents[-200:]
                ids = ids[-200:]

            # 第一轮：用内容哈希去重（O(N)）
            hash_groups: dict[str, list[int]] = {}
            for i, doc in enumerate(documents):
                h = self._simple_hash(doc)
                hash_groups.setdefault(h, []).append(i)

            to_merge = []
            seen = set()

            # 从哈希相同的组中找合并候选
            for group_indices in hash_groups.values():
                if len(group_indices) > 1:
                    for idx in group_indices:
                        seen.add(idx)
                    to_merge.append(group_indices)

            # 第二轮：对哈希不同但文本相似的条目做抽样比较（O(N*K)，K=窗口大小）
            unseen = [i for i in range(len(documents)) if i not in seen]
            window_size = 50  # 只与最近 50 条比较
            for i in unseen:
                group = [i]
                # 只与窗口内的条目比较
                start = max(0, i - window_size)
                for j in range(start, i):
                    if j in seen or j == i:
                        continue
                    if self._text_overlap(documents[i], documents[j]) > 0.7:
                        group.append(j)
                        seen.add(j)
                if len(group) > 1:
                    to_merge.append(group)
                    seen.add(i)

            # 执行合并：保留最长的，删除其余
            merged_count = 0
            for group in to_merge:
                longest_idx = max(group, key=lambda i: len(documents[i]))
                remove_ids = [ids[i] for i in group if i != longest_idx]
                if remove_ids:
                    try:
                        memory.collection.delete(ids=remove_ids)
                        merged_count += len(remove_ids)
                    except Exception as e:
                        logger.debug("Merge delete failed: %s", e)

            return merged_count

        except Exception as e:
            logger.warning("Merge redundant memories failed: %s", e)
            return 0

    def _archive_outdated_memories(self, memory: "MemoryStore",
                                    npc_states: dict,
                                    current_day: int) -> int:
        """归档过时的记忆（已死亡 NPC 的日常行为等）"""
        try:
            all_memories = memory.collection.get()
            if not all_memories or not all_memories["ids"]:
                return 0

            archived_count = 0
            archive_ids = []

            for i, meta in enumerate(all_memories["metadatas"]):
                if not meta:
                    continue

                # 归档条件 1：超过 100 天的普通叙事
                day = meta.get("day", 0)
                mem_type = meta.get("type", "")
                if (day > 0 and current_day - day > 100 and
                        mem_type == "narrative"):
                    archive_ids.append(all_memories["ids"][i])
                    continue

                # 归档条件 2：已死亡 NPC 的记忆
                if mem_type == "npc_action":
                    npc_id = meta.get("npc_id", "")
                    if npc_id in npc_states:
                        npc = npc_states[npc_id]
                        if hasattr(npc, 'is_dead') and npc.is_dead:
                            archive_ids.append(all_memories["ids"][i])

            # 执行归档
            if archive_ids:
                # 先保存到归档区
                for aid in archive_ids[:20]:  # 每次最多归档20条
                    try:
                        idx = all_memories["ids"].index(aid)
                        self._archived_memories.append({
                            "id": aid,
                            "text": all_memories["documents"][idx],
                            "metadata": all_memories["metadatas"][idx],
                            "archived_day": current_day,
                        })
                    except (ValueError, IndexError):
                        pass

                # 限制归档区大小
                if len(self._archived_memories) > 200:
                    self._archived_memories = self._archived_memories[-200:]

                # 从活跃记忆中删除
                try:
                    memory.collection.delete(ids=archive_ids[:20])
                    archived_count = min(20, len(archive_ids))
                except Exception as e:
                    logger.debug("Archive delete failed: %s", e)

            return archived_count

        except Exception as e:
            logger.warning("Archive outdated memories failed: %s", e)
            return 0

    def _extract_player_patterns(self, memory: "MemoryStore",
                                  player_state: "PlayerState") -> list[str]:
        """提取玩家行为模式"""
        if not player_state or not player_state.memory:
            return []

        patterns = []

        # 从短期记忆分析行为倾向
        recent_actions = player_state.memory.short_term[-10:]
        if not recent_actions:
            return []

        action_text = "\n".join(recent_actions)

        # 使用 LLM 提取模式
        prompt = f"""分析以下玩家近期行为，提取行为模式。

【近期行为】
{action_text[:1000]}

【玩家标签】
{', '.join(player_state.tags[:10])}

【提取要求】
只提取明确的、可操作的行为模式。每个模式一句话。

【输出JSON格式】
{{"patterns": ["模式1", "模式2"]}}

最多5个模式。只输出JSON。"""

        try:
            result = self.llm.chat_json(prompt, temperature=0.3, max_tokens=512)
            patterns = result.get("patterns", [])
        except Exception as e:
            logger.debug("Pattern extraction failed: %s", e)

        return patterns[:5]

    def _update_lorebook(self, lorebook: "Lorebook",
                          player_state: "PlayerState",
                          world_state: "WorldState",
                          npc_states: dict,
                          current_day: int) -> list[str]:
        """更新 lorebook 条目"""
        updates = []

        # 更新 NPC 身份条目
        for npc_id, npc in npc_states.items():
            if npc.role_history:
                latest_change = npc.role_history[-1]
                if latest_change.get("day", 0) == current_day:
                    lorebook.update_npc_entry(npc.name, npc.get_identity_summary())
                    updates.append(f"NPC {npc.name} 身份更新")

        return updates

    @staticmethod
    def _simple_hash(text: str) -> str:
        """快速内容哈希，用于第一轮去重"""
        # 取前 50 字符 + 长度作为粗粒度哈希
        return f"{len(text)}_{text[:50]}"

    @staticmethod
    def _text_overlap(a: str, b: str) -> float:
        """计算两个文本的词级重叠率"""
        if not a or not b:
            return 0.0
        words_a = set(a.split())
        words_b = set(b.split())
        if not words_a or not words_b:
            return 0.0
        intersection = len(words_a & words_b)
        min_len = min(len(words_a), len(words_b))
        return intersection / min_len if min_len > 0 else 0.0

    def restore_archived(self, memory: "MemoryStore",
                         memory_id: str) -> bool:
        """从归档区恢复一条记忆"""
        for i, archived in enumerate(self._archived_memories):
            if archived["id"] == memory_id:
                try:
                    memory.add_memory(
                        archived["text"],
                        archived.get("metadata", {})
                    )
                    self._archived_memories.pop(i)
                    return True
                except Exception as e:
                    logger.warning("Restore archived memory failed: %s", e)
                    return False
        return False

    def get_curate_stats(self) -> dict:
        """返回整理统计"""
        return {
            "total_curations": len(self._curate_history),
            "archived_memories": len(self._archived_memories),
            "last_curate": self._curate_history[-1] if self._curate_history else None,
        }

    def to_dict(self) -> dict:
        """序列化用于存档"""
        return {
            "last_curate_turn": self._last_curate_turn,
            "last_summary_turn": self._last_summary_turn,
            "summary_counter": self._summary_counter,
            "curate_history": self._curate_history[-10:],
            "archived_memories": self._archived_memories[-100:],
            "history_summaries": self._history_summaries,
        }

    def from_dict(self, data: dict):
        """从存档恢复"""
        self._last_curate_turn = data.get("last_curate_turn", 0)
        self._last_summary_turn = data.get("last_summary_turn", 0)
        self._summary_counter = data.get("summary_counter", 0)
        self._curate_history = data.get("curate_history", [])
        self._archived_memories = data.get("archived_memories", [])
        self._history_summaries = data.get("history_summaries", [])
