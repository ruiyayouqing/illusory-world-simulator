"""角色动态状态分离（CHIRON 式）：稳定信息 + 变化信息分离管理。

参考 CHIRON（EMNLP 2024）：角色状态向量化，显式追踪"什么在什么时候为真"。
将角色信息拆分为：
  - 稳定信息（角色卡）：姓名、职业、性格等长期不变的属性
  - 变化信息（动态状态）：心情、压力、已知事实、伤势、目标等随剧情演变的属性

每回合后自动分析角色变化并结构化存储，下回合加载最新状态，解决"人物崩坏"问题。
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger("chronoverse.character_state")


@dataclass
class StateChange:
    """单次状态变更记录。"""
    turn: int
    day: int
    trigger_event: str  # 触发事件描述
    field_name: str  # 变更的字段
    old_value: str  # 变更前值（字符串描述）
    new_value: str  # 变更后值
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class DynamicState:
    """
    角色动态状态：追踪角色随时间变化的信息。
    与角色卡（稳定信息）分离。
    """
    # 心理状态
    mood: str = "平静"  # 当前心情
    emotional_state: str = "稳定"  # 情绪状态
    stress_level: int = 0  # 压力等级 0-100

    # 关系动态（最近变化）
    recent_relation_changes: list[dict] = field(default_factory=list)  # [{"npc_name": str, "change": str, "turn": int}]

    # 掌握的信息（角色"知道什么"）
    known_facts: list[str] = field(default_factory=list)  # 角色知道的事实
    known_secrets: list[str] = field(default_factory=list)  # 角色知道的秘密
    misinformation: list[str] = field(default_factory=list)  # 角色的错误认知

    # 物理状态
    injuries: list[dict] = field(default_factory=list)  # [{"part": str, "severity": str, "day": int}]
    current_location: str = ""

    # 阶段目标
    short_term_goal: str = ""  # 短期目标
    long_term_goal: str = ""  # 长期目标
    current_objective: str = ""  # 当前行动目标

    # 行为模式（近期）
    recent_behaviors: list[str] = field(default_factory=list)  # 最近的行为模式
    behavior_tendency: str = ""  # 行为倾向描述

    # 变更历史
    change_history: list[StateChange] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mood": self.mood,
            "emotional_state": self.emotional_state,
            "stress_level": self.stress_level,
            "recent_relation_changes": self.recent_relation_changes[-10:],  # 只保留最近10条
            "known_facts": self.known_facts[-50:],  # 最多50条
            "known_secrets": self.known_secrets[-20:],
            "misinformation": self.misinformation[-10:],
            "injuries": [i for i in self.injuries if i.get("severity") != "healed"],
            "current_location": self.current_location,
            "short_term_goal": self.short_term_goal,
            "long_term_goal": self.long_term_goal,
            "current_objective": self.current_objective,
            "recent_behaviors": self.recent_behaviors[-20:],
            "behavior_tendency": self.behavior_tendency,
            "change_history": [c.__dict__ for c in self.change_history[-30:]],  # 最多30条
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DynamicState":
        changes = [StateChange(**c) for c in data.get("change_history", [])]
        return cls(
            mood=data.get("mood", "平静"),
            emotional_state=data.get("emotional_state", "稳定"),
            stress_level=data.get("stress_level", 0),
            recent_relation_changes=data.get("recent_relation_changes", []),
            known_facts=data.get("known_facts", []),
            known_secrets=data.get("known_secrets", []),
            misinformation=data.get("misinformation", []),
            injuries=data.get("injuries", []),
            current_location=data.get("current_location", ""),
            short_term_goal=data.get("short_term_goal", ""),
            long_term_goal=data.get("long_term_goal", ""),
            current_objective=data.get("current_objective", ""),
            recent_behaviors=data.get("recent_behaviors", []),
            behavior_tendency=data.get("behavior_tendency", ""),
            change_history=changes,
        )

    def record_change(self, turn: int, day: int, event: str, field_name: str, old_val: str, new_val: str):
        """记录状态变更。"""
        change = StateChange(
            turn=turn, day=day, trigger_event=event,
            field_name=field_name, old_value=old_val, new_value=new_val
        )
        self.change_history.append(change)
        if len(self.change_history) > 30:
            self.change_history = self.change_history[-30:]

    def to_prompt(self) -> str:
        """生成注入 prompt 的动态状态描述。"""
        parts = []
        if self.mood != "平静" or self.emotional_state != "稳定":
            parts.append(f"当前心情：{self.mood}（{self.emotional_state}）")
        if self.stress_level > 30:
            parts.append(f"压力等级：{self.stress_level}/100")
        if self.injuries:
            injury_desc = "、".join(f"{i['part']}{i['severity']}" for i in self.injuries)
            parts.append(f"伤势：{injury_desc}")
        if self.current_objective:
            parts.append(f"当前目标：{self.current_objective}")
        if self.short_term_goal:
            parts.append(f"短期目标：{self.short_term_goal}")
        if self.recent_behaviors:
            parts.append(f"近期行为：{'、'.join(self.recent_behaviors[-3:])}")
        if self.known_facts:
            parts.append(f"已知信息：{'；'.join(self.known_facts[-5:])}")
        if self.known_secrets:
            parts.append(f"掌握秘密：{'；'.join(self.known_secrets[-3:])}")
        if self.misinformation:
            parts.append(f"（错误认知：{'；'.join(self.misinformation[-2:])}）")

        return "\n".join(parts) if parts else ""


class CharacterStateManager:
    """角色状态管理器：管理所有角色的动态状态。"""

    def __init__(self, llm=None):
        self._states: dict[str, DynamicState] = {}  # entity_id -> DynamicState
        self.llm = llm

    def get_state(self, entity_id: str) -> DynamicState:
        """获取角色动态状态，不存在则创建。"""
        if entity_id not in self._states:
            self._states[entity_id] = DynamicState()
        return self._states[entity_id]

    def set_state(self, entity_id: str, state: DynamicState):
        self._states[entity_id] = state

    def analyze_changes_from_narrative(
        self, entity_id: str, narrative: str, turn: int, day: int
    ) -> dict[str, Any]:
        """
        从叙事文本中分析角色状态变化。
        使用 LLM 提取结构化状态变更。
        """
        if not self.llm:
            return {}

        state = self.get_state(entity_id)
        current_state_desc = state.to_prompt()

        prompt = f"""请分析以下叙事文本中该角色的状态变化。

【角色当前动态状态】
{current_state_desc or "（无记录）"}

【叙事文本】
{narrative}

请提取该角色在本段叙事中的状态变化，返回 JSON：
{{
    "mood": "心情（如有变化）",
    "emotional_state": "情绪状态（如有变化）",
    "stress_level": 压力等级0-100（如有变化）,
    "new_facts": ["新获知的事实"],
    "new_secrets": ["新获知的秘密"],
    "new_misinformation": ["新的错误认知"],
    "injuries": [{{"part": "部位", "severity": "轻/中/重", "action": "add/remove"}}],
    "location_change": "新位置（如有变化）",
    "short_term_goal": "短期目标（如有变化）",
    "current_objective": "当前目标（如有变化）",
    "behaviors": ["观察到的行为模式"],
    "trigger_event": "触发状态变化的关键事件"
}}

只返回 JSON，只包含有变化的字段。"""

        try:
            result = self.llm.chat_json(prompt, temperature=0.3)
            if result and "error" not in result:
                self._apply_changes(entity_id, result, turn, day)
                return result
        except Exception as e:
            logger.warning("Character state analysis failed for %s: %s", entity_id, e)

        return {}

    def _apply_changes(self, entity_id: str, changes: dict, turn: int, day: int):
        """将分析出的变更应用到动态状态。"""
        state = self.get_state(entity_id)
        trigger = changes.get("trigger_event", "")

        if "mood" in changes and changes["mood"]:
            old = state.mood
            state.mood = changes["mood"]
            state.record_change(turn, day, trigger, "mood", old, state.mood)

        if "emotional_state" in changes and changes["emotional_state"]:
            old = state.emotional_state
            state.emotional_state = changes["emotional_state"]
            state.record_change(turn, day, trigger, "emotional_state", old, state.emotional_state)

        if "stress_level" in changes:
            old = str(state.stress_level)
            state.stress_level = max(0, min(100, int(changes["stress_level"])))
            state.record_change(turn, day, trigger, "stress_level", old, str(state.stress_level))

        for fact in changes.get("new_facts", []):
            if fact and fact not in state.known_facts:
                state.known_facts.append(fact)
                state.record_change(turn, day, trigger, "known_facts", "", fact)

        for secret in changes.get("new_secrets", []):
            if secret and secret not in state.known_secrets:
                state.known_secrets.append(secret)
                state.record_change(turn, day, trigger, "known_secrets", "", secret)

        for misinfo in changes.get("new_misinformation", []):
            if misinfo and misinfo not in state.misinformation:
                state.misinformation.append(misinfo)
                state.record_change(turn, day, trigger, "misinformation", "", misinfo)

        for injury in changes.get("injuries", []):
            if injury.get("action") == "remove":
                state.injuries = [i for i in state.injuries if i.get("part") != injury.get("part")]
            else:
                state.injuries.append({"part": injury.get("part", ""), "severity": injury.get("severity", "轻"), "day": day})
            state.record_change(turn, day, trigger, "injuries", "", str(injury))

        if changes.get("location_change"):
            old = state.current_location
            state.current_location = changes["location_change"]
            state.record_change(turn, day, trigger, "location", old, state.current_location)

        if changes.get("short_term_goal"):
            old = state.short_term_goal
            state.short_term_goal = changes["short_term_goal"]
            state.record_change(turn, day, trigger, "short_term_goal", old, state.short_term_goal)

        if changes.get("current_objective"):
            old = state.current_objective
            state.current_objective = changes["current_objective"]
            state.record_change(turn, day, trigger, "current_objective", old, state.current_objective)

        for behavior in changes.get("behaviors", []):
            if behavior:
                state.recent_behaviors.append(behavior)
                if len(state.recent_behaviors) > 20:
                    state.recent_behaviors = state.recent_behaviors[-20:]

    def get_state_for_prompt(self, entity_id: str) -> str:
        """获取角色的动态状态描述，用于注入 prompt。"""
        state = self._states.get(entity_id)
        if not state:
            return ""
        return state.to_prompt()

    def to_dict(self) -> dict:
        return {eid: state.to_dict() for eid, state in self._states.items()}

    def from_dict(self, data: dict):
        self._states = {eid: DynamicState.from_dict(d) for eid, d in data.items()}

    def get_stats(self) -> dict:
        return {
            "tracked_characters": len(self._states),
            "total_changes": sum(len(s.change_history) for s in self._states.values()),
        }
