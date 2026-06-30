"""
[v9] 事件总线 — 发布/订阅模式，解耦子系统间的直接调用。

用法：
    bus = EventBus()
    bus.on("player_input", handler_fn)
    bus.emit("player_input", {"input": "攻击哥布林"})

所有子系统通过 EventBus 通信，不再直接调用彼此的方法。
插件系统也挂载到 EventBus 上，监听相同的事件。
"""
from __future__ import annotations
import logging
from collections import defaultdict
from typing import Callable, Any

logger = logging.getLogger("chronoverse.event_bus")


class EventBus:
    """轻量级事件总线：支持同步/异步处理器，支持优先级"""

    # 预定义的事件类型
    EVENT_TYPES = [
        "on_turn_start",        # 回合开始
        "on_turn_end",          # 回合结束
        "on_player_input",      # 玩家输入
        "on_npc_action",        # NPC行动完成
        "on_narrative_generated",  # 叙事生成完成
        "on_world_event",       # 世界事件触发
        "on_day_change",        # 日期变更
        "on_economy_update",    # 经济更新
        "on_npc_evolved",       # NPC演化完成
        "on_save",              # 存档事件
        "on_load",              # 读档事件
        "on_player_death",      # 玩家死亡
        "on_level_up",          # 升级
    ]

    def __init__(self):
        # event_name -> [(priority, handler, plugin_name)]
        self._handlers: dict[str, list[tuple[int, Callable, str]]] = defaultdict(list)

    @property
    def _handler_count(self) -> int:
        """动态计算当前注册的处理器总数（避免 on/off 计数不一致）"""
        return sum(len(h) for h in self._handlers.values())

    def on(self, event: str, handler: Callable, priority: int = 100,
           plugin_name: str = ""):
        """
        注册事件处理器。
        
        Args:
            event: 事件名称
            handler: 处理函数，接收 (event_data: dict) 参数
            priority: 优先级，数字越小越先执行（默认100）
            plugin_name: 插件名称（用于调试和卸载）
        """
        self._handlers[event].append((priority, handler, plugin_name))
        self._handlers[event].sort(key=lambda x: x[0])
        logger.debug("EventBus: registered handler for '%s' (priority=%d, plugin=%s)",
                      event, priority, plugin_name or "core")

    def off(self, event: str, handler: Callable = None, plugin_name: str = ""):
        """移除事件处理器"""
        if event not in self._handlers:
            return
        if handler:
            self._handlers[event] = [
                (p, h, n) for p, h, n in self._handlers[event] if h != handler
            ]
        elif plugin_name:
            self._handlers[event] = [
                (p, h, n) for p, h, n in self._handlers[event] if n != plugin_name
            ]

    def off_plugin(self, plugin_name: str):
        """移除某插件的所有事件处理器"""
        for event in list(self._handlers.keys()):
            self._handlers[event] = [
                (p, h, n) for p, h, n in self._handlers[event] if n != plugin_name
            ]

    def emit(self, event: str, data: dict = None) -> list[Any]:
        """
        触发事件，按优先级依次调用所有处理器。
        
        Args:
            event: 事件名称
            data: 事件数据字典
            
        Returns:
            所有处理器的返回值列表
        """
        if data is None:
            data = {}
        results = []
        handlers = self._handlers.get(event, [])
        if not handlers:
            return results

        for priority, handler, plugin_name in handlers:
            try:
                result = handler(data)
                if result is not None:
                    results.append(result)
            except Exception as e:
                logger.warning("EventBus: handler error for '%s' (plugin=%s): %s",
                               event, plugin_name or "core", e)
        return results

    def get_stats(self) -> dict:
        """返回事件总线统计信息"""
        return {
            "total_handlers": self._handler_count,
            "events": {
                event: len(handlers)
                for event, handlers in self._handlers.items()
                if handlers
            },
        }

    def clear(self):
        """清除所有处理器"""
        self._handlers.clear()
