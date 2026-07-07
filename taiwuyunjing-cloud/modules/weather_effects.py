from __future__ import annotations
import random
from .schemas import PlayerState, WorldState
from .llm.base_llm import BaseLLM
from .prompt_utils import resolve_location_name  # [Bug] location code → display name


class WeatherEffects:
    def __init__(self, llm: BaseLLM):
        self.llm = llm
        self.current_effects: dict = {}

    def apply_weather(self, player: PlayerState, world_state: WorldState) -> dict:
        weather = world_state.weather
        season = world_state.season
        world_type = world_state.world_type

        prompt = f"""根据当前天气和世界类型，判断对玩家的影响。

【天气】{weather}
【季节】{season}
【世界类型】{world_type}
【玩家位置】{resolve_location_name(player.location, world_state)}  # [Bug] location code → display name
【玩家状态】体力{player.stats.energy}/{player.stats.max_energy}

【输出JSON格式】
{{
    "travel_modifier": 0.0到1.0,
    "energy_cost": 0到20,
    "mood_change": -3到3,
    "special_effect": "特殊效果描述（如：采药成功率提升、视野受限等）",
    "narrative": "100字的天气氛围描写",
    "bonuses": {{"行动类型": 倍率}}
}}"""
        response = self.llm.chat_json(prompt, temperature=0.5)

        energy_cost = response.get("energy_cost", 0)
        if energy_cost > 0:
            player.stats.energy = max(0, player.stats.energy - energy_cost)

        self.current_effects = response
        return {
            "weather": weather,
            "energy_cost": energy_cost,
            "travel_modifier": response.get("travel_modifier", 1.0),
            "narrative": response.get("narrative", ""),
            "special_effect": response.get("special_effect", ""),
        }

    def get_travel_modifier(self) -> float:
        return self.current_effects.get("travel_modifier", 1.0)

    def get_special_effect(self) -> str:
        return self.current_effects.get("special_effect", "")

    def get_weather_narrative(self, world_state: WorldState) -> str:
        return self.current_effects.get("narrative", f"天气是{world_state.weather}。")
