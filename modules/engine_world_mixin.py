"""
[v9] 世界生成 Mixin — 从 GameEngine 抽取的世界创建逻辑
"""
from __future__ import annotations
import re
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .game_engine import GameEngine

from .level_system import LevelSystem, GodsCodex

logger = logging.getLogger("chronoverse")


class WorldGenMixin:
    """世界生成相关方法"""

    def generate_world_from_description(self: "GameEngine", description: str, world_type: str = "custom") -> str:
        if not self.world_generator:
            return ""
        # 世界生成是核心任务，使用主模型；主模型不可用时回退到 router（含 cheap 兜底）
        from .world_generator import WorldGenerator
        if self.main_llm:
            temp_generator = WorldGenerator(self.main_llm)
            world_data = temp_generator.generate_world(description, world_type)
        elif self.llm:
            temp_generator = WorldGenerator(self.llm)
            world_data = temp_generator.generate_world(description, world_type)
        else:
            return ""
        if "error" in world_data:
            raise RuntimeError(f"世界生成失败: {world_data['error']}")
        raw_player = world_data.get("player_start", {})

        def _safe_int(val, default=0):
            if isinstance(val, int):
                return val
            if isinstance(val, str):
                m = re.search(r'\d+', val)
                return int(m.group()) if m else default
            return default

        age_val = _safe_int(raw_player.get("age", 18), 18)
        raw_stats = {k: _safe_int(v) for k, v in raw_player.get("stats", {}).items()}
        raw_gold = _safe_int(raw_player.get("starting_gold", 100), 100)
        raw_rep = _safe_int(raw_player.get("reputation", 0), 0)

        raw_max_age = raw_player.get("max_age")
        if raw_max_age is None:
            lifespans = {"historical": 80, "modern": 85, "wuxia": 120, "xianxia": 500,
                         "fantasy": 200, "scifi": 150, "postapocalyptic": 60, "custom": 80}
            raw_max_age = lifespans.get(world_data.get("world_type", world_type), 80)
        else:
            raw_max_age = _safe_int(raw_max_age, 80)

        player_data = {
            "name": raw_player.get("name", "无名"),
            "age": age_val, "max_age": raw_max_age,
            "current_goal": raw_player.get("background", "活下去"),
            "location": raw_player.get("starting_location", ""),
            "stats": raw_stats,
            "social": {"position": raw_player.get("position", "无名氏"), "reputation": raw_rep, "gold": raw_gold},
            "tags": raw_player.get("tags", ["普通人"]),
            "inventory": {"gold": raw_gold, "items": raw_player.get("starting_items", [])},
        }

        npc_data_list = []
        for npc_id, npc_info in world_data.get("npcs", {}).items():
            raw_rel = npc_info.get("relation_to_player", {})
            if isinstance(raw_rel, str):
                raw_rel = {"favor": 50, "relation_type": raw_rel}
            npc_data_list.append({
                "agent_id": npc_id,
                "name": npc_info.get("name", npc_id),
                "age": _safe_int(npc_info.get("age", 25), 25),
                "personality": npc_info.get("personality", ""),
                "tags": npc_info.get("tags", []),
                "current_location": npc_info.get("initial_location", ""),
                "relation_to_player": {"favor": raw_rel.get("favor", 50), "relation_type": raw_rel.get("relation_type", "陌生人")},
                "ai_behavior": {
                    "personality_traits": npc_info.get("tags", []),
                    "current_goal": npc_info.get("goals", ""),
                    "long_term_goal": npc_info.get("long_term_goal", ""),
                    "short_term_goals": npc_info.get("short_term_goals", []),
                },
            })

        world_id = self.create_new_game(world_data, player_data, npc_data_list, world_data.get("world_name", "新世界"))

        system_map = {"wuxia": "martial", "martial": "martial", "xianxia": "cultivation",
                      "cultivation": "cultivation", "fantasy": "magic", "magic": "magic"}
        system_type = system_map.get(world_data.get("world_type"), "none")
        self.current_level_system_type = system_type
        self.level_system = LevelSystem(system_type)
        self.gods_codex = GodsCodex()
        self.gods_codex.initialize_default_rules(world_data.get("world_type", "custom"))
        return world_id

    def get_world_types(self: "GameEngine") -> list[dict]:
        if self.world_generator:
            return self.world_generator.get_world_types()
        return []
