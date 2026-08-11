"""
[v1.2] 视野隔离硬执行模块 — 在 LLM 调用前过滤 NPC 可感知的信息。

设计原则（参考用户需求与 AI 方案）：
  - 视野隔离是「硬执行」而非「软建议」：sleeping 区 NPC 直接跳过 LLM 思考
  - LLM prompt 中只注入 NPC 视野内的信息（可见 NPC / 可见事件 / 远方传闻）
  - knowledge_scope.forbidden_knowledge 字段在 prompt 生成时硬过滤
  - 与 npc_perception 协同：复用其 zone 分类与 _same_area 判定

核心方法：
  1. should_skip_thinking(npc) → bool
     判定是否跳过 LLM 思考（休眠中 / sleeping 区）
  2. filter_visible_npcs(npc, all_npcs) → list[NPCState]
     返回 NPC 视野内的其他 NPC
  3. filter_visible_events(npc, event_log) → list[dict]
     返回 NPC 视野内的事件（远处大事件降级为传闻）
  4. build_perception_brief(npc) → str
     构造 LLM prompt 用的感知摘要
  5. enforce_knowledge_scope(npc, text) → str
     从文本中剔除 forbidden_knowledge 关键词

集成点：
  - GoalEvaluator: 跳过 sleeping NPC 的 LLM 判定；prompt 注入 perception_brief
  - BranchPlanner: 跳过 sleeping NPC 的规划；prompt 注入 perception_brief
  - game_engine._sync_v12_engines: 注入 player/all_npcs/world_state/event_log_provider
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .schemas import NPCState, PlayerState, WorldState
    from .npc_perception import NPCPerceptionSystem

logger = logging.getLogger("chronoverse.perception_scope")


class PerceptionScope:
    """视野隔离硬执行器。

    用法：
        scope = PerceptionScope(perception_system)
        scope.set_context(player=..., all_npcs=..., world_state=..., event_log_provider=...)
        if scope.should_skip_thinking(npc):
            return  # 跳过 LLM 调用
        brief = scope.build_perception_brief(npc)
        # 把 brief 拼到 LLM prompt 中
    """

    # 远处事件降级为传闻的严重度阈值
    RUMOR_SEVERITY_LEVELS = ("critical", "major")

    def __init__(self, perception_system: "NPCPerceptionSystem | None" = None):
        self.perception = perception_system
        # 运行时引用（由 game_engine._sync_v12_engines 注入）
        self._player: "PlayerState | None" = None
        self._all_npcs: dict | None = None
        self._world_state: "WorldState | None" = None
        # event_log_provider: 返回当前 event_log_today 的回调（避免循环引用 engine）
        self._event_log_provider: Callable[[], list] | None = None

    # ── 上下文注入 ──────────────────────────────────────────

    def set_context(
        self,
        player: "PlayerState | None" = None,
        all_npcs: dict | None = None,
        world_state: "WorldState | None" = None,
        event_log_provider: Callable[[], list] | None = None,
    ):
        """由 game_engine._sync_v12_engines 调用，注入运行时引用。

        所有参数可选，仅更新非 None 的字段。
        """
        if player is not None:
            self._player = player
        if all_npcs is not None:
            self._all_npcs = all_npcs
        if world_state is not None:
            self._world_state = world_state
        if event_log_provider is not None:
            self._event_log_provider = event_log_provider

    def bind_perception(self, perception_system: "NPCPerceptionSystem | None"):
        """绑定/重新绑定底层 NPCPerceptionSystem（用于 game_engine 启动时）"""
        self.perception = perception_system

    # ── 硬执行：是否跳过 LLM 思考 ───────────────────────────

    def should_skip_thinking(
        self,
        npc: "NPCState",
        world_state: "WorldState | None" = None,
        player: "PlayerState | None" = None,
    ) -> bool:
        """判定 NPC 是否应跳过 LLM 思考。

        跳过条件：
          1. NPC 处于 is_dormant 状态（休眠中不该思考）
          2. NPC 已故/昏迷/垂死/囚禁
          3. NPC 在 sleeping 感知区（远离玩家，无思考必要，节省成本）

        Returns:
            True 表示跳过 LLM 调用，由规则降级处理
        """
        # 休眠 NPC 不参与思考
        # [功能二] 玩家手动隐藏的 NPC 也不参与思考
        if getattr(npc, "is_dormant", False) or getattr(npc, "hidden", False):
            return True

        # 已故/昏迷等状态跳过
        if "已故" in (npc.tags or []):
            return True
        if any(s in (npc.status_effects or []) for s in ("昏迷", "垂死", "囚禁")):
            return True

        # 无感知系统时不强制跳过（向后兼容）
        if not self.perception:
            return False

        ws = world_state or self._world_state
        pl = player or self._player
        if not pl or not ws:
            return False

        try:
            zone = self.perception.classify_npc_zone(npc, pl, ws)
            return zone == "sleeping"
        except Exception as e:
            logger.debug("[PerceptionScope] zone classify failed for %s: %s",
                         getattr(npc, "name", "?"), e)
            return False

    def get_zone(
        self,
        npc: "NPCState",
        world_state: "WorldState | None" = None,
        player: "PlayerState | None" = None,
    ) -> str:
        """获取 NPC 当前感知区（active/aware/rumor/sleeping）"""
        if getattr(npc, "is_dormant", False) or getattr(npc, "hidden", False):
            return "sleeping"
        if not self.perception:
            return "active"
        ws = world_state or self._world_state
        pl = player or self._player
        if not pl or not ws:
            return "active"
        try:
            return self.perception.classify_npc_zone(npc, pl, ws)
        except Exception:
            return "rumor"

    # ── 信息过滤 ────────────────────────────────────────────

    def filter_visible_npcs(
        self,
        npc: "NPCState",
        all_npcs: dict | None = None,
    ) -> list:
        """返回 NPC 视野内的其他 NPC 列表（active + aware 区）。

        - 同 location → active（直接可见）
        - 同区域（_same_area 判定）→ aware（感知到存在）
        - 其他 → 不在视野内
        - 跳过休眠/已故 NPC
        """
        all_npcs = all_npcs if all_npcs is not None else (self._all_npcs or {})
        npc_loc = npc.current_location or ""
        visible: list = []

        for other_id, other in all_npcs.items():
            if other_id == npc.agent_id:
                continue
            # [功能二] 隐藏的 NPC 不应被其他 NPC 感知到
            if getattr(other, "is_dormant", False) or getattr(other, "hidden", False):
                continue
            if "已故" in (other.tags or []):
                continue

            other_loc = other.current_location or ""
            if not other_loc:
                continue

            # 同地点 → active
            if other_loc == npc_loc:
                visible.append(other)
                continue

            # 同区域 → aware
            if self.perception and self.perception._same_area(npc_loc, other_loc):
                visible.append(other)

        return visible

    def filter_visible_events(
        self,
        npc: "NPCState",
        event_log: list | None = None,
    ) -> list:
        """返回 NPC 视野内的事件。

        - 同 location 的事件 → 直接可见
        - 同区域事件 → 可感知
        - 远处的 critical/major 事件 → 降级为传闻（带 _rumor 标记）
        - 远处的 normal/minor 事件 → 不可见
        - 无 location 字段的事件：critical/major 当作传闻，其他不可见
        """
        if event_log is None:
            if self._event_log_provider:
                try:
                    event_log = self._event_log_provider() or []
                except Exception:
                    event_log = []
            else:
                event_log = []

        npc_loc = npc.current_location or ""
        visible: list = []

        for event in event_log:
            if not isinstance(event, dict):
                continue

            event_loc = event.get("location") or event.get("event_location") or ""
            severity = event.get("severity", event.get("impact_level", "normal"))

            # 无 location 字段的事件
            if not event_loc:
                if severity in self.RUMOR_SEVERITY_LEVELS:
                    rumor_event = dict(event)
                    rumor_event["_rumor"] = True
                    visible.append(rumor_event)
                continue

            # 同地点
            if event_loc == npc_loc:
                visible.append(event)
                continue

            # 同区域
            if self.perception and self.perception._same_area(npc_loc, event_loc):
                visible.append(event)
                continue

            # 远处大事件 → 传闻
            if severity in self.RUMOR_SEVERITY_LEVELS:
                rumor_event = dict(event)
                rumor_event["_rumor"] = True
                # 标记描述为传闻
                desc = rumor_event.get("description", "")
                if not desc.startswith("（传闻）"):
                    rumor_event["description"] = f"（传闻）{desc}"
                visible.append(rumor_event)

        return visible

    # ── LLM Prompt 摘要构造 ─────────────────────────────────

    def build_perception_brief(
        self,
        npc: "NPCState",
        all_npcs: dict | None = None,
        event_log: list | None = None,
        world_state: "WorldState | None" = None,
        player: "PlayerState | None" = None,
    ) -> str:
        """构造 NPC 当前可感知的环境摘要（注入 LLM prompt）。

        包含：
          - 感知区与所在地
          - 视野内的其他人物（最多 5 个）
          - 视野内/传闻事件（最近 3 条）
          - 知识边界提示（forbidden_knowledge）
        """
        ws = world_state or self._world_state
        pl = player or self._player
        zone = self.get_zone(npc, ws, pl)

        parts: list[str] = []
        parts.append(f"感知区：{zone}")

        if npc.current_location:
            parts.append(f"所在地：{npc.current_location}")

        # 可见 NPC
        all_npcs = all_npcs if all_npcs is not None else (self._all_npcs or {})
        if all_npcs:
            visible = self.filter_visible_npcs(npc, all_npcs)
            if visible:
                names = []
                for n in visible[:5]:
                    role = n.role or "居民"
                    names.append(f"{n.name}({role})")
                parts.append(f"视野内人物：{', '.join(names)}")
            else:
                parts.append("视野内人物：无")

        # 可见事件
        if event_log is None and self._event_log_provider:
            try:
                event_log = self._event_log_provider() or []
            except Exception:
                event_log = []

        if event_log:
            visible_events = self.filter_visible_events(npc, event_log)
            if visible_events:
                ev_list = []
                for e in visible_events[-3:]:
                    desc = e.get("description") or e.get("summary") or ""
                    if desc:
                        ev_list.append(f"- {desc[:60]}")
                if ev_list:
                    parts.append("近期事件：\n" + "\n".join(ev_list))

        # 知识边界提示
        ks = getattr(npc, "knowledge_scope", {}) or {}
        forbidden = ks.get("forbidden_knowledge", [])
        if forbidden:
            parts.append(f"（你不知道：{', '.join(forbidden[:3])}）")

        return "\n".join(parts)

    # ── 知识边界硬过滤 ──────────────────────────────────────

    def enforce_knowledge_scope(self, npc: "NPCState", text: str) -> str:
        """从文本中剔除 forbidden_knowledge 关键词（硬过滤）。

        用于在拼装 LLM prompt 后做最后一道过滤，防止 forbidden 信息泄露。
        """
        if not text:
            return text
        ks = getattr(npc, "knowledge_scope", {}) or {}
        forbidden = ks.get("forbidden_knowledge", [])
        if not forbidden:
            return text

        result = text
        for fb in forbidden:
            if fb and isinstance(fb, str) and fb in result:
                result = result.replace(fb, "***")
        return result

    def get_visible_npc_names(
        self,
        npc: "NPCState",
        all_npcs: dict | None = None,
    ) -> list[str]:
        """便捷方法：返回可见 NPC 的名字列表（用于规则判定时的目标过滤）"""
        visible = self.filter_visible_npcs(npc, all_npcs)
        return [n.name for n in visible]


# ── 全局单例 ──────────────────────────────────────────────

_global_scope: PerceptionScope | None = None


def get_perception_scope() -> PerceptionScope:
    """获取全局 PerceptionScope 单例"""
    global _global_scope
    if _global_scope is None:
        _global_scope = PerceptionScope()
    return _global_scope


def set_perception_scope(scope: PerceptionScope):
    """注入 PerceptionScope（由 game_engine 启动时调用）"""
    global _global_scope
    _global_scope = scope
