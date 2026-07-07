"""NPC 技能自学库（Voyager/Hermes 式）：从成功交互中提取可复用技能。

核心思路（借鉴 Voyager 自学 Minecraft 技能 + Hermes Agent 技能自学）：
  1. NPC 执行动作后，从成功交互中用 LLM 提取可复用的行为策略/话术模板
  2. 抽象为"技能"存入向量库（语义检索）+ 内存索引（有效性排序）
  3. 后续遇到类似场景时检索复用，越用越聪明
  4. 与 npc_procedural_memory.py 互补：
     - 程序性记忆记录"动作类型-上下文-成功率"统计
     - 技能库提取"可复用的策略/话术模板"（更抽象、更可迁移）

设计原则：
  - 每个 NPC 有独立的技能空间，有容量上限
  - 技能学习是后台/异步操作，失败不影响主流程
  - 相似技能自动合并并更新成功率，避免膨胀
  - 技能按"有效性 × 场景匹配度"检索
"""
from __future__ import annotations
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("chronoverse.skill_library")


@dataclass
class Skill:
    """NPC 技能：可复用的行为策略或话术模板。"""
    skill_id: str
    name: str  # 技能名称
    description: str  # 技能描述
    skill_type: str  # "combat"|"social"|"trade"|"exploration"|"survival"|"craft"|"study"
    context_pattern: str  # 适用场景描述
    action_template: str  # 行动模板（可含占位符）
    success_count: int = 0  # 成功次数
    fail_count: int = 0  # 失败次数
    success_rate: float = 0.5  # 成功率
    learned_turn: int = 0  # 学习回合
    learned_day: int = 0  # 学习天数
    last_used_turn: int = 0  # 最后使用回合
    tags: list[str] = field(default_factory=list)  # 标签
    source_memory: str = ""  # 来源记忆描述

    @property
    def total_uses(self) -> int:
        return self.success_count + self.fail_count

    @property
    def effectiveness(self) -> float:
        """综合有效性评分（成功率 × 使用次数加成）。"""
        if self.total_uses == 0:
            return 0.5
        # 使用次数越多，评分越可信
        confidence = min(1.0, self.total_uses / 10)
        return self.success_rate * confidence + 0.5 * (1 - confidence)

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "skill_type": self.skill_type,
            "context_pattern": self.context_pattern,
            "action_template": self.action_template,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "success_rate": self.success_rate,
            "learned_turn": self.learned_turn,
            "learned_day": self.learned_day,
            "last_used_turn": self.last_used_turn,
            "tags": self.tags,
            "source_memory": self.source_memory,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Skill":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class NPCSkillLibrary:
    """
    NPC 技能自学库。
    从成功交互中提取技能，存储到向量库，后续检索复用。
    """

    def __init__(self, llm=None, memory_store=None):
        self.llm = llm
        self.memory_store = memory_store
        self._skills: dict[str, dict[str, Skill]] = {}  # npc_id -> {skill_id -> Skill}
        self._max_skills_per_npc: int = 30
        self._skill_index: dict[str, list[str]] = {}  # npc_id -> [skill_id] 按有效性排序

    def set_memory_store(self, memory_store):
        """注入向量库（MemoryStore 在世界加载后由 game_engine 注入）。"""
        self.memory_store = memory_store

    def learn_from_success(
        self, npc_id: str, npc_name: str, context: str, action: str,
        result: str, turn: int, day: int
    ) -> Skill | None:
        """
        从成功交互中学习技能。
        """
        if not self.llm:
            return None

        # 1. 用 LLM 提取可复用技能
        skill = self._extract_skill(npc_name, context, action, result, turn, day)
        if not skill:
            return None

        # 2. 检查是否已有相似技能
        existing = self._find_similar_skill(npc_id, skill)
        if existing:
            # 更新现有技能的成功计数
            existing.success_count += 1
            existing.success_rate = existing.success_count / max(1, existing.total_uses)
            existing.last_used_turn = turn
            self._update_index(npc_id)
            logger.debug("NPC %s updated skill '%s' (success #%d)", npc_name, existing.name, existing.success_count)
            return existing

        # 3. 存储新技能
        if npc_id not in self._skills:
            self._skills[npc_id] = {}

        self._skills[npc_id][skill.skill_id] = skill

        # 4. 存入向量库（用于语义检索）
        if self.memory_store:
            try:
                self.memory_store.add_memory_with_importance(
                    text=f"[技能] {skill.name}: {skill.description} | 场景: {skill.context_pattern} | 模板: {skill.action_template}",
                    metadata={"type": "skill", "skill_id": skill.skill_id, "npc_id": npc_id},
                    importance=0.7,
                    memory_type="semantic",
                )
            except Exception as e:
                logger.warning("Failed to store skill in vector DB: %s", e)

        # 5. 限制数量
        self._trim_skills(npc_id)
        self._update_index(npc_id)

        logger.info("NPC %s learned new skill '%s' (%s)", npc_name, skill.name, skill.skill_type)
        return skill

    def record_failure(self, npc_id: str, skill_id: str, turn: int):
        """记录技能使用失败。"""
        skill = self._skills.get(npc_id, {}).get(skill_id)
        if skill:
            skill.fail_count += 1
            skill.success_rate = skill.success_count / max(1, skill.total_uses)
            skill.last_used_turn = turn
            self._update_index(npc_id)

    def _extract_skill(self, npc_name: str, context: str, action: str, result: str, turn: int, day: int) -> Skill | None:
        """用 LLM 从成功交互中提取技能。"""
        prompt = f"""请从以下成功交互中提取一个可复用的技能。

【角色】{npc_name}

【场景上下文】
{context}

【执行的行动】
{action}

【结果】
{result}

【要求】
提取一个可复用的行为策略或话术模板，使其能在类似场景中再次使用。

返回 JSON：
{{
    "name": "技能名称（简洁）",
    "description": "技能描述",
    "skill_type": "combat|social|trade|exploration|survival|craft|study",
    "context_pattern": "适用场景描述（什么情况下使用）",
    "action_template": "行动模板（可含{{target}}、{{item}}等占位符）",
    "tags": ["标签1", "标签2"]
}}"""

        try:
            # [v10.6] 技能数据使用 chat_json，不用 "narrative" schema（技能没有 narrative 字段）
            result_data = self.llm.chat_json(prompt, temperature=0.3)

            if not result_data or "error" in result_data:
                return None

            return Skill(
                skill_id=f"skill_{uuid.uuid4().hex[:8]}",
                name=result_data.get("name", "未命名技能"),
                description=result_data.get("description", ""),
                skill_type=result_data.get("skill_type", "social"),
                context_pattern=result_data.get("context_pattern", ""),
                action_template=result_data.get("action_template", ""),
                learned_turn=turn,
                learned_day=day,
                last_used_turn=turn,
                tags=result_data.get("tags", []),
                source_memory=f"{action[:100]}",
            )
        except Exception as e:
            logger.warning("Skill extraction failed: %s", e)
            return None

    def _find_similar_skill(self, npc_id: str, new_skill: Skill) -> Skill | None:
        """查找相似技能。"""
        npc_skills = self._skills.get(npc_id, {})
        for skill in npc_skills.values():
            # 名称相似或场景模式相似
            if (skill.name == new_skill.name or
                skill.skill_type == new_skill.skill_type and
                self._text_similarity(skill.context_pattern, new_skill.context_pattern) > 0.7):
                return skill
        return None

    def _text_similarity(self, a: str, b: str) -> float:
        """字符级 Jaccard 相似度。"""
        if not a or not b:
            return 0.0
        set_a = set(a)
        set_b = set(b)
        return len(set_a & set_b) / len(set_a | set_b) if (set_a | set_b) else 0.0

    def _trim_skills(self, npc_id: str):
        """裁剪技能数量。"""
        skills = self._skills.get(npc_id, {})
        if len(skills) <= self._max_skills_per_npc:
            return

        # 按有效性排序，保留最好的
        sorted_skills = sorted(skills.values(), key=lambda s: s.effectiveness, reverse=True)
        keep_ids = {s.skill_id for s in sorted_skills[:self._max_skills_per_npc]}
        self._skills[npc_id] = {sid: s for sid, s in skills.items() if sid in keep_ids}

    def _update_index(self, npc_id: str):
        """更新技能索引（按有效性排序）。"""
        skills = self._skills.get(npc_id, {})
        sorted_skills = sorted(skills.values(), key=lambda s: s.effectiveness, reverse=True)
        self._skill_index[npc_id] = [s.skill_id for s in sorted_skills]

    def get_relevant_skills(self, npc_id: str, context: str, top_k: int = 3) -> list[Skill]:
        """
        获取与当前上下文相关的技能。
        优先返回高有效性 + 场景匹配的技能。
        """
        skills = self._skills.get(npc_id, {})
        if not skills:
            return []

        # 计算每个技能的匹配度
        scored = []
        for skill in skills.values():
            # 场景匹配度（简单关键词匹配）
            match_score = self._context_match(skill.context_pattern, context)
            # 综合评分 = 有效性 × 0.6 + 匹配度 × 0.4
            total_score = skill.effectiveness * 0.6 + match_score * 0.4
            scored.append((skill, total_score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in scored[:top_k]]

    def _context_match(self, pattern: str, context: str) -> float:
        """场景模式匹配度。"""
        if not pattern or not context:
            return 0.0
        # 提取模式中的关键词
        keywords = [k.strip() for k in pattern.replace("，", " ").replace(",", " ").split() if len(k.strip()) >= 2]
        if not keywords:
            return 0.0
        matched = sum(1 for k in keywords if k in context)
        return matched / len(keywords)

    def get_skills_for_prompt(self, npc_id: str, context: str, top_k: int = 3) -> str:
        """获取技能描述，用于注入 prompt。"""
        skills = self.get_relevant_skills(npc_id, context, top_k)
        if not skills:
            return ""

        parts = []
        for skill in skills:
            parts.append(f"- {skill.name}（成功率{skill.success_rate:.0%}）：{skill.action_template}")

        return "【可用技能】\n" + "\n".join(parts)

    def batch_learn(self, interactions: list[dict], turn: int, day: int) -> int:
        """
        批量学习：从多个成功交互中学习技能。
        interactions: [{"npc_id": str, "npc_name": str, "context": str, "action": str, "result": str}]
        返回学习到的技能数。
        """
        learned = 0
        for inter in interactions:
            try:
                skill = self.learn_from_success(
                    inter["npc_id"], inter["npc_name"],
                    inter.get("context", ""), inter.get("action", ""),
                    inter.get("result", ""), turn, day
                )
                if skill:
                    learned += 1
            except Exception as e:
                logger.warning("Batch learn failed for %s: %s", inter.get("npc_id", ""), e)

        if learned:
            logger.info("Batch skill learning: %d skills learned", learned)
        return learned

    def to_dict(self) -> dict:
        return {
            "skills": {
                npc_id: {sid: s.to_dict() for sid, s in skills.items()}
                for npc_id, skills in self._skills.items()
            },
        }

    def from_dict(self, data: dict):
        self._skills = {
            npc_id: {sid: Skill.from_dict(s) for sid, s in skills.items()}
            for npc_id, skills in data.get("skills", {}).items()
        }
        for npc_id in self._skills:
            self._update_index(npc_id)

    def get_stats(self) -> dict:
        total_skills = sum(len(skills) for skills in self._skills.values())
        type_counts: dict[str, int] = {}
        for skills in self._skills.values():
            for skill in skills.values():
                type_counts[skill.skill_type] = type_counts.get(skill.skill_type, 0) + 1
        return {
            "npcs_with_skills": len(self._skills),
            "total_skills": total_skills,
            "type_distribution": type_counts,
        }
