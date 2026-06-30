"""
统一 Agent 基类

参考 AIvilization 的统一 agent 架构，将玩家和 NPC 的核心逻辑抽象为共享基类。
所有 agent 共享：规划、记忆、标签管理、关系管理、上下文构建。
"""
from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from .schemas import PlayerState, NPCState, WorldState, RelationEntry

if TYPE_CHECKING:
    from .llm.base_llm import BaseLLM
    from .db.chroma_db import MemoryStore
    from .lorebook import Lorebook
    from .branch_planner import BranchPlanner

logger = logging.getLogger("chronoverse.agent_base")


class BaseAgent(ABC):
    """统一 Agent 基类：玩家和 NPC 共享核心逻辑"""

    def __init__(self, llm: BaseLLM, memory: MemoryStore = None,
                 lorebook: Lorebook = None):
        self.llm = llm
        self.memory = memory
        self.lorebook = lorebook
        self.planner: BranchPlanner | None = None
        self._last_context_debug: dict = {}
        # [v10+] 混合检索器（可选注入，用于 BM25 + 向量 + GraphRAG 融合检索）
        self.hybrid_retriever = None

    def set_hybrid_retriever(self, hybrid_retriever):
        """注入混合检索器。"""
        self.hybrid_retriever = hybrid_retriever

    # ── 抽象接口 ──────────────────────────────────────────

    @abstractmethod
    def plan_next_action(self, agent_state, world_state: WorldState,
                         context: dict = None) -> dict:
        """规划下一步行动（子类实现）"""

    @abstractmethod
    def execute_action(self, action: dict, agent_state,
                       world_state: WorldState) -> dict:
        """执行行动（子类实现）"""

    # ── 标签管理（共享）────────────────────────────────────

    @staticmethod
    def apply_tags(state, new_tags: list[str], removed_tags: list[str],
                   max_tags: int = 30):
        """通用标签增删，适用于玩家和 NPC"""
        for tag in new_tags:
            if tag not in state.tags and len(state.tags) < max_tags:
                state.tags.append(tag)
        for tag in removed_tags:
            if tag in state.tags:
                state.tags.remove(tag)

    @staticmethod
    def consolidate_tags(tags: list[str], merge_map: dict = None,
                         max_tags: int = 25) -> list[str]:
        """合并同义标签，去重"""
        if not merge_map:
            return list(dict.fromkeys(tags))[:max_tags]
        consolidated = []
        seen = set()
        for tag in tags:
            merged = merge_map.get(tag, tag)
            if merged not in seen:
                seen.add(merged)
                consolidated.append(merged)
        return consolidated[:max_tags]

    # ── 状态效果管理（共享）──────────────────────────────

    @staticmethod
    def apply_effects(state, new_effects: list[str],
                      removed_effects: list[str], max_effects: int = 15):
        """通用状态效果增删"""
        for eff in new_effects:
            if eff not in state.status_effects and len(state.status_effects) < max_effects:
                state.status_effects.append(eff)
        for eff in removed_effects:
            if eff in state.status_effects:
                state.status_effects.remove(eff)

    @staticmethod
    def consolidate_effects(effects: list[str],
                            priority_keywords: list[str] = None,
                            max_effects: int = 10) -> list[str]:
        """优先保留关键效果，超限时裁剪低优先级"""
        if len(effects) <= max_effects:
            return effects
        if not priority_keywords:
            priority_keywords = ["中毒", "重伤", "诅咒", "封印", "濒死", "疯狂"]
        prioritized = [e for e in effects
                       if any(k in e for k in priority_keywords)]
        others = [e for e in effects if e not in prioritized]
        return prioritized + others[:max_effects - len(prioritized)]

    # ── 关系管理（共享）────────────────────────────────────

    @staticmethod
    def apply_relation_changes(state, changes,
                               npc_names: list[str] = None,
                               npc_states: dict = None):
        """通用关系变更：支持模糊匹配 NPC 名称。
        [v10.5] 兼容 LLM 返回 list 格式（自动转换为 dict）。"""
        # [v10.5] 类型兼容：LLM 有时返回 list 而非 dict
        if isinstance(changes, list):
            changes = {c.get("npc_id", c.get("name", "")): c for c in changes if isinstance(c, dict)}
        if not isinstance(changes, dict):
            return

        name_map = {}
        if npc_names:
            for n in npc_names:
                name_map[n.lower()] = n
                name_map[n] = n

        for npc_id, change in changes.items():
            # 模糊匹配
            matched_name = npc_id
            if npc_id in name_map:
                matched_name = name_map[npc_id]
            elif npc_id.lower() in name_map:
                matched_name = name_map[npc_id.lower()]
            elif npc_names:
                for n in npc_names:
                    if npc_id in n or n in npc_id:
                        matched_name = n
                        break
            if matched_name == npc_id and npc_id.isascii():
                continue

            rt = change.get("relation_type", "")
            fv = change.get("favor", 0)
            if matched_name in state.relations:
                rel = state.relations[matched_name]
                rel.favor = max(0, min(100, rel.favor + fv))
                if rt:
                    rel.relation_type = rt
                if "description" in change:
                    rel.description = change["description"]
            else:
                state.relations[matched_name] = RelationEntry(
                    favor=max(0, min(100, 50 + fv)),
                    relation_type=rt or "陌生人",
                    description=change.get("description", ""),
                )
            # 同步更新 NPC 侧
            if npc_states and matched_name in npc_states:
                npc = npc_states[matched_name]
                npc.relation_to_player.favor = state.relations[matched_name].favor
                if rt:
                    npc.relation_to_player.relation_type = rt

    # ── 记忆管理（共享）────────────────────────────────────

    def update_memory(self, state, event_text: str, day: int,
                      max_short_term: int = 20):
        """更新短期记忆，溢出时合并到长期摘要"""
        state.memory.short_term.append(f"第{day}天: {event_text}")
        if len(state.memory.short_term) > max_short_term:
            overflow = state.memory.short_term[:len(state.memory.short_term) - max_short_term]
            summary = "; ".join(overflow)
            state.memory.long_term_summary += f" {summary}"
            MAX_LONG_TERM_SUMMARY_LEN = 2000
            if len(state.memory.long_term_summary) > MAX_LONG_TERM_SUMMARY_LEN:
                # 超限时保留最新部分，避免无限增长
                state.memory.long_term_summary = state.memory.long_term_summary[-MAX_LONG_TERM_SUMMARY_LEN:]
            state.memory.short_term = state.memory.short_term[-max_short_term:]

    # ── RAG 检索（共享）────────────────────────────────────

    def build_rag_context(self, player_input: str, current_turn: int = 0,
                          scene_type=None) -> str:
        """
        从向量库检索相关历史和伏笔。
        [v10] 使用带重要性+时间衰减的排序检索。
        [v10+] 优先使用混合检索（BM25 + 向量 + GraphRAG），失败回退到纯向量检索。
        [v10+] scene_type: 叙事场景类型，用于动态调整检索权重（GraphRAG 动态启停）。
        """
        if not self.memory:
            return ""
        parts = []

        # [v10+] 优先使用混合检索（如果已注入）
        narratives = None
        if self.hybrid_retriever is not None:
            try:
                narratives = self.hybrid_retriever.retrieve(
                    player_input, top_k=5, current_turn=current_turn,
                    scene_type=scene_type,
                )
                if narratives:
                    logger.debug("Hybrid retrieval returned %d results", len(narratives))
            except Exception as e:
                logger.warning("Hybrid retrieval failed, falling back to vector: %s", e)
                narratives = None

        # 回退：[v10] 使用 ranked search 如果可用
        if not narratives:
            try:
                narratives = self.memory.search_memory_ranked(
                    player_input, n_results=5, current_turn=current_turn
                )
            except AttributeError:
                # 向后兼容：v9 的 MemoryStore 没有 search_memory_ranked
                narratives = self.memory.search_memory(player_input, n_results=5)

        if narratives:
            nar_texts = [n["text"][:500] for n in narratives if n.get("text")]
            if nar_texts:
                parts.append("【向量库检索：相关历史】\n" +
                             "\n".join(f"- {t}" for t in nar_texts))
        foreshadows = self.memory.search_foreshadow(player_input, n_results=3)
        if foreshadows:
            fs_texts = [f["text"][:500] for f in foreshadows if f.get("text")]
            if fs_texts:
                parts.append("【向量库检索：伏笔/重要线索】\n" +
                             "\n".join(f"- {t}" for t in fs_texts))
        if not parts:
            return ""
        return "\n" + "\n".join(parts) + "\n请参考以上检索到的历史信息，保持叙事连贯性。\n"

    # ── 身份审计（共享）────────────────────────────────────

    def audit_identity_consistency(self, narrative: str, npc_states: dict,
                                   day: int = 0) -> list[dict]:
        """检查叙事中 NPC 身份是否与数据库一致"""
        if not narrative or not npc_states:
            return []
        npc_inventory = []
        for nid, npc in npc_states.items():
            summary = npc.get_identity_summary()
            npc_inventory.append(f"  {nid}: {summary}")

        audit_prompt = f"""你是角色身份审计员。根据下面的【数据库身份档案】和【新生成的叙事】，检查叙事中每个NPC的身份是否与数据库一致。

【数据库身份档案】
{chr(10).join(npc_inventory)}

【新生成的叙事】
{narrative[:1200]}

【审计规则】
1. 只关注实质性身份变化（职业/关系/地位变更）
2. 不关注临时行为
3. 一致则不报告，不一致必须报告
4. 区分合理剧情演变 vs AI错误

【输出JSON】
{{"discrepancies": [{{"npc_id": "...", "db_role": "...", "narrative_role": "...", "reason": "...", "is_legitimate_change": true/false, "suggested_fix": "..."}}]}}
只输出JSON。"""
        try:
            result = self.llm.chat_json(audit_prompt, temperature=0.2, max_tokens=0)
            return result.get("discrepancies", [])
        except Exception as e:
            logger.warning("Identity audit LLM failed: %s", e)
            return []

    # ── 上下文调试 ─────────────────────────────────────────

    def get_context_debug(self) -> dict:
        """返回最近一次上下文构建的调试信息"""
        return self._last_context_debug
