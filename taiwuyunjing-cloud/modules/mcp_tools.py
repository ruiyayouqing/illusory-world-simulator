"""MCP（Model Context Protocol）兼容工具层：标准化工具接入。

本模块实现一个轻量级 MCP 兼容层，不依赖外部 MCP SDK，但遵循 MCP 的设计理念。
MCP 定义了工具（Tools）、资源（Resources）、提示（Prompts）三种原语，
此处实现 Tools 原语：将世界任务板、蝴蝶效应审批、外部工具封装为标准化的工具接口，
为第三方插件生态铺路。

设计要点：
  - ToolDefinition 描述工具元信息（名称、描述、输入 JSON Schema、处理函数）
  - MCPToolRegistry 管理工具注册、调用、统计、限流
  - register_builtin_tools 注册内置工具，绑定到 GameEngine 的子系统
  - 工具调用失败时返回 ToolResult(success=False)，不影响主流程
"""
from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("chronoverse.mcp")


@dataclass
class ToolDefinition:
    """MCP 工具定义。"""
    name: str  # 工具名称
    description: str  # 工具描述
    input_schema: dict  # 输入参数 JSON Schema
    handler: Callable | None = None  # 处理函数
    category: str = "general"  # 工具类别
    enabled: bool = True
    rate_limit: int = 0  # 速率限制（次/分钟），0=不限
    last_called: float = 0.0  # 上次调用时间
    call_count: int = 0  # 调用次数
    error_count: int = 0  # 错误次数

    def to_mcp_format(self) -> dict:
        """转换为 MCP 协议格式。"""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


@dataclass
class ToolResult:
    """工具调用结果。"""
    success: bool
    data: Any = None
    error: str = ""
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "metadata": self.metadata,
        }


class MCPToolRegistry:
    """
    MCP 工具注册表。
    管理所有可用工具，处理工具调用。
    遵循 MCP 设计理念但不依赖外部 SDK。
    """

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}
        self._call_history: list[dict] = []
        self._max_history: int = 200

    def register(self, tool: ToolDefinition) -> bool:
        """注册工具。"""
        if tool.name in self._tools:
            logger.warning("Tool '%s' already registered, overwriting", tool.name)
        self._tools[tool.name] = tool
        logger.info("Registered MCP tool: %s (%s)", tool.name, tool.category)
        return True

    def unregister(self, name: str) -> bool:
        """注销工具。"""
        if name in self._tools:
            del self._tools[name]
            logger.info("Unregistered MCP tool: %s", name)
            return True
        return False

    def list_tools(self, category: str | None = None) -> list[dict]:
        """列出所有可用工具。"""
        tools = []
        for tool in self._tools.values():
            if not tool.enabled:
                continue
            if category and tool.category != category:
                continue
            tools.append(tool.to_mcp_format())
        return tools

    def call(self, name: str, arguments: dict) -> ToolResult:
        """调用工具。"""
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(success=False, error=f"Tool '{name}' not found")

        if not tool.enabled:
            return ToolResult(success=False, error=f"Tool '{name}' is disabled")

        # 速率限制检查
        if tool.rate_limit > 0:
            now = time.time()
            if now - tool.last_called < 60.0 / tool.rate_limit:
                return ToolResult(success=False, error=f"Tool '{name}' rate limited")

        if not tool.handler:
            return ToolResult(success=False, error=f"Tool '{name}' has no handler")

        # 调用工具
        start_time = time.time()
        try:
            result = tool.handler(**arguments) if arguments else tool.handler()
            tool.call_count += 1
            tool.last_called = time.time()

            # 记录调用历史
            self._record_call(name, arguments, True, time.time() - start_time)

            if isinstance(result, ToolResult):
                return result
            return ToolResult(success=True, data=result)

        except Exception as e:
            tool.error_count += 1
            tool.last_called = time.time()
            self._record_call(name, arguments, False, time.time() - start_time, str(e))
            logger.warning("MCP tool '%s' failed: %s", name, e)
            return ToolResult(success=False, error=str(e))

    def _record_call(self, name: str, arguments: dict, success: bool,
                     duration: float, error: str = ""):
        """记录调用历史。"""
        self._call_history.append({
            "tool": name,
            "arguments": arguments,
            "success": success,
            "duration": duration,
            "error": error,
            "timestamp": time.time(),
        })
        if len(self._call_history) > self._max_history:
            self._call_history = self._call_history[-self._max_history:]

    def get_stats(self) -> dict:
        """获取统计信息。"""
        total_calls = sum(t.call_count for t in self._tools.values())
        total_errors = sum(t.error_count for t in self._tools.values())
        return {
            "total_tools": len(self._tools),
            "enabled_tools": sum(1 for t in self._tools.values() if t.enabled),
            "total_calls": total_calls,
            "total_errors": total_errors,
            "error_rate": total_errors / max(1, total_calls),
            "tools": {
                name: {
                    "calls": t.call_count,
                    "errors": t.error_count,
                    "category": t.category,
                    "enabled": t.enabled,
                }
                for name, t in self._tools.items()
            },
        }


# ===== 内置工具实现 =====

def register_builtin_tools(registry: MCPToolRegistry, engine=None):
    """注册内置工具。engine 为 GameEngine 实例，工具处理函数通过闭包绑定。"""

    # 1. 世界任务板工具
    registry.register(ToolDefinition(
        name="world_task_board.list",
        description="获取世界任务板上的任务列表",
        input_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["pending", "ready", "running", "completed", "failed", "expired"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50}
            }
        },
        handler=lambda status=None, limit=20: _list_world_tasks(engine, status, limit),
        category="world_task_board",
    ))

    registry.register(ToolDefinition(
        name="world_task_board.create",
        description="在世界任务板上创建新任务",
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "required_role": {"type": "string"},
                "priority": {"type": "string", "enum": ["low", "normal", "high", "critical"]}
            },
            "required": ["title", "description"]
        },
        handler=lambda title, description, required_role="", priority="normal": _create_world_task(engine, title, description, required_role, priority),
        category="world_task_board",
    ))

    registry.register(ToolDefinition(
        name="world_task_board.assign",
        description="将任务分配给NPC",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "npc_id": {"type": "string"}
            },
            "required": ["task_id", "npc_id"]
        },
        handler=lambda task_id, npc_id: _assign_world_task(engine, task_id, npc_id),
        category="world_task_board",
    ))

    # 2. 蝴蝶效应审批工具
    registry.register(ToolDefinition(
        name="butterfly.evaluate",
        description="评估玩家行为的世界影响",
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "context": {"type": "string"}
            },
            "required": ["action"]
        },
        handler=lambda action, context="": _evaluate_butterfly(engine, action, context),
        category="butterfly_effect",
    ))

    registry.register(ToolDefinition(
        name="butterfly.get_pending",
        description="获取待审批的蝴蝶效应",
        input_schema={
            "type": "object",
            "properties": {}
        },
        handler=lambda: _get_pending_butterfly(engine),
        category="butterfly_effect",
    ))

    registry.register(ToolDefinition(
        name="butterfly.approve",
        description="审批蝴蝶效应",
        input_schema={
            "type": "object",
            "properties": {
                "approval_id": {"type": "string"},
                "decision": {"type": "string", "enum": ["approve", "reject", "modify"]}
            },
            "required": ["approval_id", "decision"]
        },
        handler=lambda approval_id, decision: _approve_butterfly(engine, approval_id, decision),
        category="butterfly_effect",
    ))

    # 3. 记忆检索工具
    registry.register(ToolDefinition(
        name="memory.search",
        description="检索记忆库",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "n_results": {"type": "integer", "minimum": 1, "maximum": 20}
            },
            "required": ["query"]
        },
        handler=lambda query, n_results=5: _search_memory(engine, query, n_results),
        category="memory",
    ))

    # 4. 世界状态工具
    registry.register(ToolDefinition(
        name="world.get_state",
        description="获取当前世界状态",
        input_schema={
            "type": "object",
            "properties": {}
        },
        handler=lambda: _get_world_state(engine),
        category="world",
    ))

    registry.register(ToolDefinition(
        name="world.advance_time",
        description="推进游戏时间",
        input_schema={
            "type": "object",
            "properties": {
                "hours": {"type": "integer"},
                "days": {"type": "integer"}
            }
        },
        handler=lambda hours=0, days=0: _advance_time(engine, hours, days),
        category="world",
    ))

    # 5. NPC 工具
    registry.register(ToolDefinition(
        name="npc.list",
        description="列出所有NPC",
        input_schema={
            "type": "object",
            "properties": {
                "location": {"type": "string"}
            }
        },
        handler=lambda location=None: _list_npcs(engine, location),
        category="npc",
    ))

    registry.register(ToolDefinition(
        name="npc.get_info",
        description="获取NPC详细信息",
        input_schema={
            "type": "object",
            "properties": {
                "npc_id": {"type": "string"}
            },
            "required": ["npc_id"]
        },
        handler=lambda npc_id: _get_npc_info(engine, npc_id),
        category="npc",
    ))

    # 6. 伏笔工具
    registry.register(ToolDefinition(
        name="foreshadow.insert",
        description="插入新伏笔",
        input_schema={
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "importance": {"type": "string", "enum": ["low", "normal", "high", "critical"]}
            },
            "required": ["content"]
        },
        handler=lambda content, importance="normal": _insert_foreshadow(engine, content, importance),
        category="foreshadow",
    ))

    registry.register(ToolDefinition(
        name="foreshadow.list",
        description="列出伏笔",
        input_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["active", "mentioned", "resolved", "deferred", "stale"]}
            }
        },
        handler=lambda status=None: _list_foreshadows(engine, status),
        category="foreshadow",
    ))

    logger.info("MCP builtin tools registered: %d tools", len(registry.list_tools()))


# ===== 工具处理函数 =====

# 优先级字符串到数值的映射（WorldTaskBoard 使用 1-10 的整数）
_PRIORITY_MAP = {"low": 3, "normal": 5, "high": 7, "critical": 9}


def _list_world_tasks(engine, status, limit):
    """列出世界任务板上的任务。"""
    if not engine or not getattr(engine, 'world_task_board', None):
        return ToolResult(success=False, error="世界任务板不可用")
    board = engine.world_task_board
    tasks = list(board.tasks.values())
    if status:
        tasks = [t for t in tasks if t.status == status]
    # 按优先级降序
    tasks.sort(key=lambda t: t.priority, reverse=True)
    return ToolResult(success=True, data=[t.to_dict() for t in tasks[:limit]])


def _create_world_task(engine, title, description, required_role, priority):
    """在世界任务板上创建新任务。"""
    if not engine or not getattr(engine, 'world_task_board', None):
        return ToolResult(success=False, error="世界任务板不可用")
    board = engine.world_task_board
    current_day = engine.world_state.current_day if engine.world_state else 0
    priority_int = _PRIORITY_MAP.get(priority, 5)
    task = board.create_task(
        title=title,
        description=description,
        task_type="world_event",
        priority=priority_int,
        created_day=current_day,
        required_role=required_role,
    )
    return ToolResult(success=True, data=task.to_dict() if task else None)


def _assign_world_task(engine, task_id, npc_id):
    """将任务手动分配给指定 NPC。"""
    if not engine or not getattr(engine, 'world_task_board', None):
        return ToolResult(success=False, error="世界任务板不可用")
    board = engine.world_task_board
    task = board.tasks.get(task_id)
    if not task:
        return ToolResult(success=False, error=f"任务 '{task_id}' 不存在")
    npc = engine.npc_states.get(npc_id) if engine.npc_states else None
    if not npc:
        return ToolResult(success=False, error=f"NPC '{npc_id}' 不存在")
    task.assigned_to = npc_id
    task.assigned_name = npc.name
    # 简单状态流转：pending/ready -> running
    if task.status in ("pending", "ready"):
        task.status = "running"
    return ToolResult(success=True, data=task.to_dict())


def _evaluate_butterfly(engine, action, context):
    """评估玩家行为的蝴蝶效应影响。"""
    if not engine or not getattr(engine, 'butterfly', None):
        return ToolResult(success=False, error="蝴蝶效应系统不可用")
    if not engine.player_state or not engine.world_state:
        return ToolResult(success=False, error="玩家或世界状态未初始化")
    full_action = f"{action}\n上下文: {context}" if context else action
    result = engine.butterfly.evaluate_impact(engine.player_state, full_action, engine.world_state)
    return ToolResult(success=True, data=result)


def _get_pending_butterfly(engine):
    """获取待审批的蝴蝶效应列表。"""
    if not engine or not getattr(engine, 'butterfly', None):
        return ToolResult(success=False, error="蝴蝶效应系统不可用")
    pending = engine.butterfly.get_pending_approvals()
    return ToolResult(success=True, data=pending)


def _approve_butterfly(engine, approval_id, decision):
    """审批蝴蝶效应后果。"""
    if not engine or not getattr(engine, 'butterfly', None):
        return ToolResult(success=False, error="蝴蝶效应系统不可用")
    result = engine.butterfly.approve_consequence(approval_id, decision)
    return ToolResult(success=True, data=result)


def _search_memory(engine, query, n_results):
    """检索记忆库。"""
    if not engine or not getattr(engine, 'memory', None):
        return ToolResult(success=False, error="记忆系统不可用")
    results = engine.memory.search_memory(query, n_results=n_results)
    return ToolResult(success=True, data=results)


def _get_world_state(engine):
    """获取当前世界状态摘要。"""
    if not engine or not engine.world_state:
        return ToolResult(success=False, error="世界状态不可用")
    ws = engine.world_state
    return ToolResult(success=True, data={
        "day": ws.current_day,
        "time": ws.current_time,
        "season": ws.season,
        "weather": ws.weather,
        "world_name": ws.world_name,
        "crisis_level": ws.crisis_level,
        "era_name": ws.era_name,
        "era_year": ws.era_year,
    })


def _advance_time(engine, hours, days):
    """推进游戏时间。"""
    if not engine or not getattr(engine, 'age_system', None):
        return ToolResult(success=False, error="时间系统不可用")
    if not engine.world_state:
        return ToolResult(success=False, error="世界状态不可用")
    total_hours = hours + days * 24
    if total_hours <= 0:
        return ToolResult(success=False, error="推进时间必须为正数")
    try:
        engine.age_system.advance_time(engine.world_state, hours=total_hours)
        return ToolResult(success=True, data={"advanced_hours": total_hours})
    except Exception as e:
        return ToolResult(success=False, error=f"推进时间失败: {e}")


def _list_npcs(engine, location):
    """列出所有 NPC，可按位置过滤。"""
    if not engine or not engine.npc_states:
        return ToolResult(success=False, error="NPC系统不可用")
    npcs = []
    for nid, npc in engine.npc_states.items():
        if location and npc.current_location != location:
            continue
        npcs.append({
            "id": nid,
            "name": npc.name,
            "role": npc.role,
            "location": npc.current_location,
        })
    return ToolResult(success=True, data=npcs)


def _get_npc_info(engine, npc_id):
    """获取 NPC 详细信息。"""
    if not engine or not engine.npc_states:
        return ToolResult(success=False, error="NPC系统不可用")
    npc = engine.npc_states.get(npc_id)
    if not npc:
        return ToolResult(success=False, error=f"NPC '{npc_id}' not found")
    # NPCState 是 Pydantic 模型，使用 model_dump 序列化
    try:
        data = npc.model_dump()
    except Exception:
        data = {"id": npc_id, "name": npc.name, "role": npc.role}
    return ToolResult(success=True, data=data)


def _insert_foreshadow(engine, content, importance):
    """插入新伏笔。"""
    if not engine or not getattr(engine, 'foreshadow_lifecycle', None):
        return ToolResult(success=False, error="伏笔系统不可用")
    current_day = engine.world_state.current_day if engine.world_state else 0
    current_turn = engine.meta.current_turn if engine.meta else 0
    hook = engine.foreshadow_lifecycle.insert(
        content=content,
        day=current_day,
        turn=current_turn,
        importance=importance,
    )
    return ToolResult(success=True, data=hook.to_dict() if hook else None)


def _list_foreshadows(engine, status):
    """列出伏笔，可按状态过滤。"""
    if not engine or not getattr(engine, 'foreshadow_lifecycle', None):
        return ToolResult(success=False, error="伏笔系统不可用")
    fl = engine.foreshadow_lifecycle
    if status:
        # 按状态过滤
        hooks = [h.to_dict() for h in fl.hooks.values() if h.status == status]
    else:
        # 默认返回活跃伏笔
        hooks = fl.get_active_hooks()
    return ToolResult(success=True, data=hooks)
