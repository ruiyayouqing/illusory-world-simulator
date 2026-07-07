# 太虚幻境 v8 插件开发指南

## 概述

太虚幻境 v8 使用钩子(Hook)机制实现插件系统。插件可以监听游戏事件、修改游戏行为、添加新功能。

## 快速开始

### 1. 创建插件文件

在 `plugins/` 目录下创建 `.py` 文件：

```python
# plugins/my_plugin.py
from plugins import PluginBase

class MyPlugin(PluginBase):
    name = "my_plugin"
    version = "1.0.0"
    description = "我的插件"
    author = "YourName"

    def on_load(self, engine):
        super().on_load(engine)
        # 注册钩子
        self.hooks["on_turn_end"] = self.on_turn_end

    def on_turn_end(self, data: dict):
        """回合结束时执行"""
        narrative = data.get("narrative", "")
        print(f"回合结束，叙事长度: {len(narrative)}")


# 必须提供 register 函数
def register(engine, register_hook_fn):
    plugin = MyPlugin()
    plugin.on_load(engine)
    for hook_name, handler in plugin.hooks.items():
        register_hook_fn(hook_name, handler)
    return plugin
```

### 2. 插件加载

插件在游戏启动时自动加载。也可以在设置中手动启用/禁用。

## 可用钩子事件

| 钩子名称 | 触发时机 | 数据参数 |
|---------|---------|---------|
| `on_turn_start` | 回合开始 | `{player_state, world_state}` |
| `on_turn_end` | 回合结束 | `{narrative, player_input, world_state, player_state}` |
| `on_player_input` | 玩家输入 | `{input, player_state}` |
| `on_npc_action` | NPC行动完成 | `{npc, action, world_state}` |
| `on_narrative_generated` | 叙事生成后 | `{narrative, day, player_input}` |
| `on_world_event` | 世界事件触发 | `{event, world_state}` |
| `on_day_change` | 日期变更 | `{old_day, new_day, world_state}` |
| `on_economy_update` | 经济更新 | `{economy, world_state}` |
| `on_npc_evolved` | NPC演化完成 | `{npc, events}` |
| `on_save` | 存档 | `{world_id}` |
| `on_load` | 读档 | `{world_id}` |
| `on_player_death` | 玩家死亡 | `{cause, player_state}` |
| `on_level_up` | 升级 | `{new_level, player_state}` |

## 访问引擎子系统

通过 `self._engine` 访问：

```python
def on_turn_end(self, data: dict):
    engine = self._engine
    if engine:
        # 访问玩家状态
        player = engine.player_state
        # 访问世界状态
        world = engine.world_state
        # 访问NPC
        npcs = engine.npc_states
        # 访问LLM
        llm = engine.llm
```

## 注册自定义 MCP 工具

[v10++] 插件可以通过 `register` 函数注册自定义 MCP（Model Context Protocol）工具，
使其可通过 `/api/mcp/tools` 列出、`/api/mcp/call` 调用。

`register` 函数的第一个参数 `svc` 是 `ServiceRegistry` 实例，
其中 `svc.mcp_registry` 是 `MCPToolRegistry` 实例。

```python
# plugins/my_plugin.py
from plugins import PluginBase
from modules.mcp_tools import ToolDefinition, ToolResult


def my_custom_handler(query: str, limit: int = 10):
    """自定义工具处理函数。返回 ToolResult 或任意值（自动包装为 success=True）。"""
    # 业务逻辑...
    return ToolResult(success=True, data={"results": [...]})


def register(svc, register_hook_fn):
    # 注册自定义 MCP 工具
    svc.mcp_registry.register(ToolDefinition(
        name="my_plugin.custom_tool",
        description="我的自定义工具",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50}
            },
            "required": ["query"]
        },
        handler=my_custom_handler,
        category="custom",
    ))
```

### MCP 工具命名规范

- 使用 `插件名.工具名` 的点分格式，如 `my_plugin.custom_tool`
- 内置工具使用子系统前缀，如 `world_task_board.list`、`butterfly.evaluate`
- 名称全局唯一，重复注册会覆盖旧定义并打印警告

### MCP 工具处理函数约定

- 处理函数接收关键字参数（与 `input_schema` 的 `properties` 对应）
- 返回 `ToolResult` 实例可直接控制成功/失败状态
- 返回其他值会被自动包装为 `ToolResult(success=True, data=返回值)`
- 抛出异常会被捕获并转为 `ToolResult(success=False, error=异常信息)`

### 内置 MCP 工具类别

| 类别 | 工具前缀 | 说明 |
|------|---------|------|
| `world_task_board` | `world_task_board.*` | 世界任务板操作 |
| `butterfly_effect` | `butterfly.*` | 蝴蝶效应审批 |
| `memory` | `memory.*` | 记忆检索 |
| `world` | `world.*` | 世界状态查询与时间推进 |
| `npc` | `npc.*` | NPC 查询 |
| `foreshadow` | `foreshadow.*` | 伏笔管理 |

## 示例插件

| 插件 | 功能 | 文件 |
|------|------|------|
| `weather_enhanced` | 天气增强效果 | `plugins/weather_enhanced.py` |
| `battle_system` | 回合制战斗 | `plugins/battle_system.py` |
| `achievements` | 成就系统 | `plugins/achievements.py` |

## 注意事项

1. **不要阻塞主线程**：耗时操作使用 `asyncio.to_thread()`
2. **异常处理**：钩子中的异常不会阻塞游戏主循环
3. **数据修改**：直接修改 `data` 字典中的对象会影响游戏状态
4. **命名规范**：插件文件名使用小写+下划线
5. **版本兼容**：检查引擎版本，确保兼容性
