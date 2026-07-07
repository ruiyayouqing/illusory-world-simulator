"""
[v9] 示例插件：天气增强效果

功能：
- 天气影响NPC行为概率（雨天减少外出，雪天增加休息）
- 天气影响战斗属性（暴风降低命中，晴天增加体力恢复）
- 天气变化叙事增强
"""
from __future__ import annotations
import logging
import random
from plugins import PluginBase

logger = logging.getLogger("chronoverse.plugin.weather")


WEATHER_EFFECTS = {
    "晴天": {"energy_regen": 1.2, "combat_accuracy": 1.0, "npc_activity": 1.0,
             "narrative": "阳光明媚，万物生机勃勃。"},
    "阴天": {"energy_regen": 1.0, "combat_accuracy": 0.95, "npc_activity": 0.9,
             "narrative": "天色阴沉，空气中弥漫着潮湿的气息。"},
    "雨天": {"energy_regen": 0.8, "combat_accuracy": 0.85, "npc_activity": 0.6,
             "narrative": "淅淅沥沥的雨声中，行人匆匆躲避。"},
    "暴雨": {"energy_regen": 0.6, "combat_accuracy": 0.7, "npc_activity": 0.3,
             "narrative": "暴雨如注，天地间一片水雾茫茫。"},
    "雪天": {"energy_regen": 0.7, "combat_accuracy": 0.8, "npc_activity": 0.5,
             "narrative": "纷纷扬扬的雪花飘落，大地银装素裹。"},
    "暴风雪": {"energy_regen": 0.4, "combat_accuracy": 0.6, "npc_activity": 0.2,
               "narrative": "狂风呼啸，暴雪肆虐，天地间一片混沌。"},
    "雾天": {"energy_regen": 0.9, "combat_accuracy": 0.75, "npc_activity": 0.7,
             "narrative": "浓雾弥漫，能见度极低，远处的景物若隐若现。"},
    "雷暴": {"energy_regen": 0.5, "combat_accuracy": 0.65, "npc_activity": 0.2,
             "narrative": "电闪雷鸣，暴雨倾盆，天地间一片骇人。"},
}


class WeatherEnhancedPlugin(PluginBase):
    name = "weather_enhanced"
    version = "1.0.0"
    description = "天气增强效果：影响NPC行为、战斗属性和叙事"
    author = "太虚幻境"

    def on_load(self, engine):
        super().on_load(engine)
        self.hooks["on_turn_start"] = self.on_turn_start
        self.hooks["on_turn_end"] = self.on_turn_end
        logger.info("天气增强插件已加载")

    def on_turn_start(self, **kwargs):
        """回合开始时应用天气效果"""
        world_state = kwargs.get("world_state")
        player_state = kwargs.get("player_state")
        if not world_state or not player_state:
            return

        weather = world_state.weather
        effects = WEATHER_EFFECTS.get(weather, WEATHER_EFFECTS["晴天"])

        # 天气影响体力恢复
        if effects["energy_regen"] != 1.0:
            regen_bonus = int(player_state.stats.max_energy * (effects["energy_regen"] - 1.0) * 0.1)
            if regen_bonus > 0:
                player_state.stats.energy = min(
                    player_state.stats.max_energy,
                    player_state.stats.energy + regen_bonus
                )

    def on_turn_end(self, **kwargs):
        """回合结束时生成天气叙事"""
        world_state = kwargs.get("world_state")
        if not world_state:
            return

        weather = world_state.weather
        effects = WEATHER_EFFECTS.get(weather, WEATHER_EFFECTS["晴天"])

        # 10%概率触发天气叙事
        if random.random() < 0.1:
            narrative = kwargs.get("narrative", "")
            if narrative and effects["narrative"] not in narrative:
                # 将天气叙事附加到主叙事
                kwargs["weather_narrative"] = effects["narrative"]


# 插件注册入口
def register(engine, register_hook_fn):
    """插件注册函数"""
    plugin = WeatherEnhancedPlugin()
    plugin.on_load(engine)
    for hook_name, handler in plugin.hooks.items():
        register_hook_fn(hook_name, handler)
    return plugin
