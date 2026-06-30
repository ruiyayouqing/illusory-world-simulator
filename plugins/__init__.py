"""
[v9] 插件系统 — 插件基类和接口定义

每个插件必须实现 PluginBase 接口：
- name: 插件名称
- version: 版本号
- hooks: 钩子处理器字典

可用钩子事件：
- on_turn_start: 回合开始 (data: {player_state, world_state})
- on_turn_end: 回合结束 (data: {narrative, player_input, world_state, player_state})
- on_player_input: 玩家输入 (data: {input, player_state})
- on_npc_action: NPC行动完成 (data: {npc, action, world_state})
- on_narrative_generated: 叙事生成后 (data: {narrative, day, player_input})
- on_world_event: 世界事件触发 (data: {event, world_state})
- on_day_change: 日期变更 (data: {old_day, new_day, world_state})
- on_economy_update: 经济更新 (data: {economy, world_state})
- on_npc_evolved: NPC演化完成 (data: {npc, events})
- on_save: 存档 (data: {world_id})
- on_load: 读档 (data: {world_id})
- on_player_death: 玩家死亡 (data: {cause, player_state})
- on_level_up: 升级 (data: {new_level, player_state})
"""
from __future__ import annotations
from abc import ABC
from typing import Callable


class PluginBase(ABC):
    """插件基类 — 所有插件必须继承此类"""

    name: str = "unnamed"
    version: str = "0.1.0"
    description: str = ""
    author: str = ""

    def __init__(self):
        self.hooks: dict[str, Callable] = {}
        self._engine = None

    def on_load(self, engine):
        """
        插件加载时调用。
        在此注册钩子处理器：self.hooks["on_turn_start"] = self.my_handler
        """
        self._engine = engine

    def on_unload(self):
        """插件卸载时调用，清理资源"""
        self.hooks.clear()
        self._engine = None

    def get_info(self) -> dict:
        """返回插件信息"""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "hooks": list(self.hooks.keys()),
        }
