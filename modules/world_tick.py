"""
[v1.5 第一期] 世界时钟 — 跨日时推进世界，生成当日事件

设计要点：
  1. 由 WorldManager.on_new_day() 在跨日时调用，不依赖真实时间
  2. 根据玩家状态/位置计算"今日打扰概率"
  3. 按概率 roll 出 0-3 个玩家事件（NPC 主动找你）
  4. 独立 10% 概率生成 1 个世界事件
  5. 节奏控制：连续 3 天有事件后强制 1 天平静；同一 NPC 7 天内不重复
  6. 全部用模板生成，不调 LLM；玩家接受事件时才由路由层调 LLM 生成桥接叙事

闭关/隐居识别：
  - player.status_effects 含 "闭关中" → 打扰概率归零（仅师门紧急传唤可破）
  - "隐居中" → 0.1
  - "重伤" → 概率 × 0.4
"""
from __future__ import annotations
import random
import logging
from typing import TYPE_CHECKING

from .world_event import (
    GameEvent, PlayerEventBus, WorldEventBus,
    pick_player_template, pick_world_template,
)

if TYPE_CHECKING:
    from .game_engine import GameEngine

logger = logging.getLogger("chronoverse.world_tick")


# 位置系数：影响打扰概率
_LOCATION_FACTOR = {
    # 无人认识的地方：低
    "荒野": 0.3, "洞府": 0.3, "隐居地": 0.3, "山林": 0.4,
    # 一般地点：1.0
    "村庄": 1.0, "城镇": 1.1, "客栈": 1.1, "市集": 1.1,
    # 人多的地方：高
    "门派": 1.3, "驻地": 1.3, "王城": 1.4, "皇宫": 1.4,
}

# 状态系数
_NEGATIVE_STATUS_FACTOR = {
    "重伤": 0.4,
    "昏迷": 0.1,
    "中毒": 0.5,
    "生病": 0.6,
}


class WorldTick:
    """世界时钟：跨日时推进世界，生成当日事件"""

    def __init__(self, engine: "GameEngine"):
        self.engine = engine
        # 节奏控制状态
        self._last_event_day: int = -10
        self._consecutive_event_days: int = 0

    def tick(self) -> dict:
        """跨日时调用，返回当日新生成的事件清单

        Returns:
            {
                "player_events": [GameEvent.to_dict(), ...],
                "world_events":  [GameEvent.to_dict(), ...],
            }
        """
        eng = self.engine
        if not eng.world_state or not eng.player_state:
            return {"player_events": [], "world_events": []}

        day = eng.world_state.current_day

        # Step 1: 清理过期事件（先清理再生成，避免误删今日新事件）
        if eng.player_event_bus:
            eng.player_event_bus.expire_old(day)
        if eng.world_event_bus:
            eng.world_event_bus.expire_old(day)

        # Step 2: 节奏控制 — 连续 3 天有事件后，强制 1 天平静
        if self._consecutive_event_days >= 3 and (day - self._last_event_day) < 2:
            logger.info("WorldTick day %d: cooldown day (skipped)", day)
            return {"player_events": [], "world_events": []}

        # Step 3: 计算今日打扰概率
        disturb_prob = self._calc_disturb_probability(eng.player_state)

        # Step 4: roll 玩家事件数量
        player_event_count = self._roll_event_count(disturb_prob)

        # Step 5: 生成玩家事件
        player_events: list[GameEvent] = []
        if player_event_count > 0 and eng.npc_states and eng.player_event_bus:
            candidates = self._pick_npc_candidates(eng.npc_states, day)
            random.shuffle(candidates)
            for npc in candidates[:player_event_count]:
                evt = self._make_player_event(npc, day)
                if evt:
                    player_events.append(evt)
                    eng.player_event_bus.add(evt)

        # Step 6: 生成世界事件（独立 10% 概率）
        world_events: list[GameEvent] = []
        if eng.world_event_bus and random.random() < 0.10:
            evt = self._make_world_event(day)
            if evt:
                world_events.append(evt)
                eng.world_event_bus.add(evt)

        # Step 7: 更新节奏控制状态
        if player_events or world_events:
            self._last_event_day = day
            self._consecutive_event_days += 1
        else:
            self._consecutive_event_days = 0

        logger.info(
            "WorldTick day %d: disturb_prob=%.2f, player=%d, world=%d",
            day, disturb_prob, len(player_events), len(world_events),
        )

        return {
            "player_events": [e.to_dict() for e in player_events],
            "world_events": [e.to_dict() for e in world_events],
        }

    # ===== 概率计算 =====

    def _calc_disturb_probability(self, player) -> float:
        """计算今日打扰概率（0.0 - 1.0）

        优先级：闭关/隐居 > 重伤等负面状态 > 位置系数
        """
        status = getattr(player, "status_effects", []) or []
        # 闭关：几乎为 0（除非师门紧急，由特殊事件触发）
        if "闭关中" in status:
            return 0.02
        if "隐居中" in status:
            return 0.10

        base = 0.55  # 基础概率

        # 负面状态系数
        for s, factor in _NEGATIVE_STATUS_FACTOR.items():
            if s in status:
                base *= factor
                break  # 只取最严重的一个

        # 位置系数
        loc = getattr(player, "location", "") or ""
        loc_factor = _LOCATION_FACTOR.get(loc, 1.0)
        base *= loc_factor

        return min(max(base, 0.0), 0.9)

    def _roll_event_count(self, prob: float) -> int:
        """根据概率决定今日事件数（0-3）

        - 1 - prob 的概率：今日 0 个事件
        - 否则按 60/30/10 分配 1/2/3 个
        """
        if random.random() > prob:
            return 0
        r = random.random()
        if r < 0.60:
            return 1
        elif r < 0.90:
            return 2
        else:
            return 3

    # ===== 候选 NPC 筛选 =====

    def _pick_npc_candidates(self, npc_states: dict, current_day: int) -> list:
        """从 NPC 池里挑主动找你的候选者

        排除规则：
          - 休眠/垂死/昏迷/囚禁的 NPC
          - 7 天内刚找过玩家的（读 recent_actions 里的 last_visit_day）
          - 好感 < 30 的陌生人（除非是仇人）
        """
        candidates = []
        for npc_id, npc in npc_states.items():
            # 状态排除
            npc_status = getattr(npc, "status_effects", []) or []
            if any(s in npc_status for s in ("休眠", "垂死", "昏迷", "囚禁", "失踪")):
                continue

            # 好感检查
            rel = getattr(npc, "relation_to_player", None)
            favor = getattr(rel, "favorability", 50) if rel else 50
            tags = getattr(npc, "tags", []) or []
            is_enemy = "仇人" in tags or favor < 0
            if favor < 30 and not is_enemy:
                continue

            # 7 天内刚找过玩家（payload 里的 last_visit_day）
            last_visit = self._get_npc_last_visit_day(npc)
            if last_visit > 0 and (current_day - last_visit) < 7:
                continue

            candidates.append(npc)
        return candidates

    def _get_npc_last_visit_day(self, npc) -> int:
        """从 NPC 的 recent_actions 中找最近一次主动找玩家的游戏日"""
        actions = getattr(npc, "recent_actions", []) or []
        for a in reversed(actions):
            if not isinstance(a, dict):
                continue
            if a.get("action_type") == "player_visit" or a.get("type") == "player_visit":
                return int(a.get("day", 0))
        return 0

    # ===== 事件生成 =====

    def _make_player_event(self, npc, day: int) -> GameEvent | None:
        """为 NPC 生成一个主动事件（模板化）

        [v1.5 第二期] 优先使用动机系统选模板，回退到第一期的好感度模板
        """
        # [v1.5 第二期] 先衰减 NPC 现有动机，再尝试 roll 新动机
        motivation_used = None
        try:
            from .motivation import get_motivation_engine
            engine = get_motivation_engine()
            engine.decay_motivations(npc, day)
            # 若 NPC 没有活跃动机，roll 一个新动机
            if not engine.pick_active_motivation(npc):
                new_motivation = engine.roll_motivation(
                    npc, self.engine.world_state, self.engine.player_state,
                )
                if new_motivation:
                    if not hasattr(npc, "motivations") or npc.motivations is None:
                        npc.motivations = []
                    npc.motivations.append(new_motivation)
            motivation_used = engine.pick_active_motivation(npc)
        except Exception as e:
            logger.warning("Motivation roll failed for %s: %s", getattr(npc, "name", "?"), e)

        # 决定事件模板：优先用动机，回退到第一期好感度模板
        rel = getattr(npc, "relation_to_player", None)
        favor = getattr(rel, "favorability", 50) if rel else 50
        # [Bug] RelationEntry 字段名是 favor 不是 favorability
        if rel and not hasattr(rel, "favorability") and hasattr(rel, "favor"):
            favor = rel.favor
        tags = getattr(npc, "tags", []) or []
        is_enemy = "仇人" in tags or favor < 0

        if motivation_used:
            try:
                from .motivation import get_motivation_engine
                engine = get_motivation_engine()
                event_type, priority, tpl = engine.pick_event_template(motivation_used["type"])
                # 动机强度高 → 提升优先级
                if motivation_used.get("intensity", 50) >= 70 and priority == "normal":
                    priority = "important"
            except Exception:
                event_type, priority, tpl = pick_player_template(favor, is_enemy)
        else:
            event_type, priority, tpl = pick_player_template(favor, is_enemy)

        name = getattr(npc, "name", npc.agent_id)
        title = tpl.format(name=name)
        # summary 包含动机原因（更利于玩家理解 NPC 来意）
        motivation_reason = motivation_used.get("reason", "") if motivation_used else ""
        summary = title + (f"（{motivation_reason}）" if motivation_reason else "")

        return GameEvent(
            category="player",
            priority=priority,
            event_type=event_type,
            title=title,
            summary=summary,
            source_npc=npc.agent_id,
            trigger_day=day,
            expire_day=day + 1,  # 玩家事件次日过期
            status="pending",
            payload={
                "npc_name": name,
                "npc_role": getattr(npc, "role", ""),
                "favorability": favor,
                "motivation_type": motivation_used["type"] if motivation_used else None,
                "motivation_intensity": motivation_used.get("intensity", 0) if motivation_used else 0,
                "motivation_reason": motivation_reason,
            },
        )

    def _make_world_event(self, day: int) -> GameEvent | None:
        """生成世界事件（每日 10% 概率，调用此方法时已 roll 过）"""
        event_type, title, summary = pick_world_template()
        return GameEvent(
            category="world",
            priority="normal",  # 世界事件默认 normal；影响玩家直接利益才升 urgent
            event_type=event_type,
            title=title,
            summary=summary,
            source_npc=None,
            trigger_day=day,
            expire_day=day + 7,  # 世界事件保留 7 天
            status="pending",
            payload={},
        )

    # ===== 持久化辅助 =====

    def state_dict(self) -> dict:
        """序列化节奏控制状态（供存档）"""
        return {
            "last_event_day": self._last_event_day,
            "consecutive_event_days": self._consecutive_event_days,
        }

    def load_state(self, data: dict) -> None:
        """从存档恢复节奏控制状态"""
        if not data:
            return
        self._last_event_day = data.get("last_event_day", -10)
        self._consecutive_event_days = data.get("consecutive_event_days", 0)
