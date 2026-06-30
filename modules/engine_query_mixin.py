"""
[v9] 子系统查询 Mixin — 从 GameEngine 抽取的简单查询方法
这些方法都是对子系统的薄封装，不包含核心逻辑。
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from .prompt_utils import resolve_location_name  # [Bug] location code → display name

if TYPE_CHECKING:
    from .game_engine import GameEngine

logger = logging.getLogger("chronoverse")


class SubsystemQueryMixin:
    """子系统查询相关方法"""

    def get_market_report(self: "GameEngine") -> str:
        if self.economy_system and self.world_state and self.world_state.economy:
            return self.economy_system.get_market_report(self.world_state.economy)
        return ""

    def get_butterfly_summary(self: "GameEngine") -> dict:
        if self.butterfly:
            # [Bug] 方法名是 get_impact_summary，不是 get_summary
            return self.butterfly.get_impact_summary()
        return {}

    def get_npc_list(self: "GameEngine") -> list[dict]:
        result = []
        for npc_id, npc in self.npc_states.items():
            rel = self.player_state.relations.get(npc.name) if self.player_state else None
            result.append({
                "id": npc_id, "name": npc.name, "age": npc.age,
                "role": npc.role, "location": npc.current_location,
                "personality": npc.personality[:50],
                "favor": rel.favor if rel else 50,
                "relation_type": rel.relation_type if rel else "陌生人",
                "mbti": npc.mbti_type or "",
            })
        return result

    def get_life_goals(self: "GameEngine") -> list[dict]:
        # [Bug] get_life_goals 在 age_system 上，不在 destiny_regret 上
        if self.age_system:
            return self.age_system.get_available_goals()
        return []

    def set_life_goal(self: "GameEngine", goal_type: str) -> bool:
        # [Bug] set_life_goal 在 age_system 上，不在 destiny_regret 上
        if self.age_system and self.player_state:
            self.age_system.set_life_goal(goal_type)
            return True
        return False

    def check_life_goal(self: "GameEngine") -> dict | None:
        # [Bug] check_life_goal 在 age_system 上，不在 destiny_regret 上
        if self.age_system and self.player_state:
            return self.age_system.check_life_goal(self.player_state)
        return None

    def get_level_info(self: "GameEngine") -> dict:
        if self.level_system:
            # [Bug] LevelSystem 没有 current_level/current_exp 属性，需用方法获取
            current = self.level_system.get_current_level()
            next_lv = self.level_system.get_next_level()
            exp_to_next = (next_lv["min_exp"] - self.level_system.experience) if next_lv else 0
            return {
                "system_type": self.level_system.system_type,
                "level": current.get("name", 0),
                "exp": self.level_system.experience,
                "exp_to_next": exp_to_next,
                "level_name": current.get("name", ""),
            }
        return {"system_type": "none", "level": 0, "exp": 0}

    def add_experience(self: "GameEngine", amount: int) -> dict:
        if self.level_system:
            return self.level_system.add_experience(amount)
        return {}

    def get_whispers(self: "GameEngine") -> list[dict]:
        if self.brain_whispers and self.player_state:
            try:
                # [Bug] 使用 location_name（如"汴京城"）而非 location code（如"bianjing"）
                loc_code = self.player_state.location
                loc_name = loc_code
                if self.world_state and hasattr(self.world_state, 'locations') and loc_code in self.world_state.locations:
                    loc_obj = self.world_state.locations[loc_code]
                    if isinstance(loc_obj, dict):
                        loc_name = loc_obj.get('location_name') or loc_obj.get('name') or loc_code
                    elif hasattr(loc_obj, 'location_name'):
                        loc_name = loc_obj.location_name or loc_code
                    elif hasattr(loc_obj, 'name'):
                        loc_name = loc_obj.name or loc_code
                context = f"第{self.world_state.current_day}天 {self.world_state.current_time}，在{loc_name}"
                return self.brain_whispers.generate_whispers(self.player_state, context, self.world_state)
            except Exception as e:
                logger.warning("Brain whispers failed: %s", e)
                return [{"category": "system", "text": "内心一片平静..."}]
        return []

    def get_full_memoir(self: "GameEngine") -> str:
        if self.memoir and self.player_state:
            # [Bug] 方法名是 generate_full_memoir，不是 get_full_memoir；需要 world_state 参数
            return self.memoir.generate_full_memoir(self.player_state, self.world_state)
        return ""

    def get_current_reflection(self: "GameEngine") -> str:
        if self.memoir and self.player_state:
            # [Bug] 方法名是 generate_current_reflection，不是 get_current_reflection
            return self.memoir.generate_current_reflection(self.player_state, self.world_state)
        return ""

    def check_favor_events(self: "GameEngine") -> list[dict]:
        if self.favor_events and self.player_state:
            return self.favor_events.check_favor_triggers(self.player_state, self.world_state)
        return []

    def check_destiny_regret(self: "GameEngine") -> dict | None:
        if self.destiny_regret and self.player_state:
            return self.destiny_regret.check_regret(self.player_state, self.world_state)
        return None

    def get_missed_summary(self: "GameEngine") -> str:
        # [Bug] get_missed_summary 不接受 player 参数
        if self.destiny_regret:
            return self.destiny_regret.get_missed_summary()
        return ""

    def get_irreversible_summary(self: "GameEngine") -> str:
        # [Bug] get_irreversible_summary 不接受 player 参数
        if self.destiny_regret:
            return self.destiny_regret.get_irreversible_summary()
        return ""

    def get_faction_wars(self: "GameEngine") -> str:
        if self.faction_wars:
            # [Bug] 方法名是 get_war_status，不是 get_active_wars_summary
            return self.faction_wars.get_war_status()
        return ""

    def get_war_history(self: "GameEngine") -> str:
        if self.faction_wars:
            return self.faction_wars.get_war_history()
        return ""

    def get_death_stats(self: "GameEngine") -> dict:
        if self.death_system:
            return self.death_system.get_death_stats()
        return {}

    def generate_better_options(self: "GameEngine") -> list[dict]:
        if not self.option_engine or not self.player_state or not self.world_state:
            return []
        scene = f"第{self.world_state.current_day}天 {self.world_state.current_time}，你在{resolve_location_name(self.player_state.location, self.world_state)}"  # [Bug] location code → display name
        return self.option_engine.generate_options(scene, self.player_state, self.world_state)

    def get_context_debug(self: "GameEngine") -> dict:
        if self.player_agent and hasattr(self.player_agent, '_last_context_debug'):
            return self.player_agent._last_context_debug
        return {}
