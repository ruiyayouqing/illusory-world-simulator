"""
[v1.2] 休眠 NPC 唤醒时的「时间跳跃推演」— 补齐休眠期间断层。

设计原则（参考 Generative Agents 时间跳跃补全）：
  - dormant NPC 唤醒时（is_dormant: True→False），调用一次 LLM 推演
  - 输入：休眠时长 + 休眠前状态 + 期间世界重大事件 + NPC 人设
  - 输出：休眠期间 NPC 的大致经历、心态变化、目标变动、状态数值变化
  - 程序将 LLM 输出应用到 NPC 状态，避免「一觉醒来一成不变」

成本控制：
  - 仅在唤醒瞬间调用一次 LLM（每个 dormant NPC 唤醒时 1 次调用）
  - dormant NPC 期间不调 LLM，纯 CPU 演化由 npc_autonomous.offline_evolve 负责
  - 推演结果写入 NPC.recent_actions 供后续决策引用

降级策略：
  - LLM 不可用时走规则降级：根据休眠时长做简单状态衰减/恢复
"""
from __future__ import annotations
import logging
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schemas import NPCState, WorldState, PlayerState
    from .llm.base_llm import BaseLLM
    from .game_engine import GameEngine

logger = logging.getLogger("chronoverse.dormant_wake")


# ===== LLM Prompt =====

DORMANT_WAKE_PROMPT = """你是虚拟世界的时间跳跃推演器。NPC 休眠了 {dormant_days} 天后重新登场，请推演这期间 ta 的大致经历。

【NPC 信息】
姓名：{npc_name}
年龄：{age}岁（增长 {age_growth} 岁）
身份：{role}
性格：{personality}
标签：{tags}
立场：{alignment}

【休眠前状态】
位置：{pre_location}
身份标签：{pre_tags}
长期目标：{long_term_goal}
短期目标：{short_term_goal}
当时情绪/状态：{pre_status}

【休眠期间世界大事】
{world_events}

【推演规则 - 必须遵守】
1. NPC 在休眠期间不能「穿越」到主角所在场景，除非 ta 主动前往
2. NPC 不能直接知晓主角的具体行动（只能听闻传闻）
3. 推演必须符合 NPC 性格与人设：胆小的不会突然勇武，贪财的不会散尽家财
4. 状态变化要合理：
   - 休眠期间体力/血量自然恢复（除非有伤患）
   - 财富小幅波动（做生意/营生）
   - 心态可能因世界大事而变化（战乱→惊慌；太平→安稳）
5. 目标演化：
   - 长期目标通常不变（除非重大变故）
   - 短期目标可能因时过境迁而调整（如「杀 X」可能因 X 已死而失效）

【输出 JSON 格式 - 严格遵循】
{{
  "dormant_summary": "休眠期间 ta 的经历概述（50-150字）",
  "state_changes": {{
    "energy_delta": 0,
    "health_delta": 0,
    "gold_delta": 0,
    "tags_add": ["新增标签"],
    "tags_remove": ["移除标签"],
    "status_add": ["新增状态效果"]
  }},
  "personality_shift": "心态变化描述（可空）",
  "goal_changes": {{
    "long_term_goal": "若变更则填新长期目标，否则空",
    "short_term_goal": "若变更则填新短期目标，否则空",
    "reason": "变更理由"
  }},
  "new_location": "若移动了则填新位置（location code 或显示名），否则空",
  "rumors_heard": ["休眠期间听闻的传闻（最多3条）"]
}}"""


class DormantWakeEvaluator:
    """休眠 NPC 唤醒时的 LLM 时间跳跃推演器。

    用法：
        evaluator = DormantWakeEvaluator(llm)
        result = evaluator.evaluate_wake(npc, world_state, engine)
        # result 包含推演结果，状态变更已应用到 npc
    """

    # 休眠天数阈值：超过此值才调 LLM，否则走规则降级
    LLM_THRESHOLD_DAYS = 7

    def __init__(self, llm: "BaseLLM | None" = None):
        self.llm = llm

    def evaluate_wake(
        self,
        npc: "NPCState",
        world_state: "WorldState",
        engine: "GameEngine | None" = None,
    ) -> dict:
        """推演休眠 NPC 唤醒时的状态变化。

        Args:
            npc: 待唤醒 NPC（is_dormant 已翻转为 False，但 dormant_since_day 仍有记录）
            world_state: 世界状态
            engine: GameEngine 引用（用于查询世界大事）

        Returns:
            {
                "evaluated": bool,
                "dormant_days": int,
                "dormant_summary": str,
                "state_changes": dict,
                "personality_shift": str,
                "goal_changes": dict,
                "new_location": str,
                "rumors_heard": list,
                "degraded": bool,  # 是否走规则降级
            }
        """
        dormant_days = self._calc_dormant_days(npc, world_state)
        if dormant_days <= 0:
            return {"evaluated": False, "dormant_days": 0}

        # 短休眠走规则降级（不调 LLM）
        if dormant_days < self.LLM_THRESHOLD_DAYS:
            return self._rule_based_wake(npc, world_state, dormant_days)

        # 长休眠走 LLM 推演
        if not self.llm:
            return self._rule_based_wake(npc, world_state, dormant_days)

        try:
            return self._llm_wake(npc, world_state, dormant_days, engine)
        except Exception as e:
            logger.warning("Dormant wake LLM failed for %s: %s", npc.name, e)
            return self._rule_based_wake(npc, world_state, dormant_days)

    # ===== LLM 推演 =====

    def _llm_wake(
        self,
        npc: "NPCState",
        world_state: "WorldState",
        dormant_days: int,
        engine: "GameEngine | None",
    ) -> dict:
        """LLM 推演休眠期间变化"""
        # 收集休眠期间世界大事
        world_events = self._collect_world_events(dormant_days, world_state, engine)

        # 休眠前状态快照（从 recent_actions 倒推，取休眠开始日之前最近的一条）
        pre_status = "正常"
        pre_tags = list(npc.tags or [])
        # 估算年龄增长（虚拟世界里 1 年 = 365 天）
        age_growth = dormant_days // 365

        prompt = DORMANT_WAKE_PROMPT.format(
            dormant_days=dormant_days,
            npc_name=npc.name,
            age=npc.age,
            age_growth=age_growth,
            role=npc.role or "普通居民",
            personality=npc.personality or "普通",
            tags="、".join(npc.tags or []) or "无",
            alignment=getattr(npc, "alignment", "中庸"),
            pre_location=npc.current_location or "未知",
            pre_tags="、".join(pre_tags) or "无",
            long_term_goal=(npc.ai_behavior or {}).get("long_term_goal", "") or "（未设定）",
            short_term_goal=(npc.ai_behavior or {}).get("current_goal", "") or "（未设定）",
            pre_status=pre_status,
            world_events=world_events or "（休眠期间世界总体平静）",
        )

        result = self.llm.chat_json(prompt, temperature=0.5, max_tokens=800)

        # [Bug] chat_json 失败时返回空 dict 而非抛异常，需手动检查
        # 若 LLM 调用失败（如 401），降级到规则推演
        if not result or not isinstance(result, dict):
            logger.warning("Dormant wake LLM returned empty for %s, degrading to rule", npc.name)
            return self._rule_based_wake(npc, world_state, dormant_days)
        # 至少要有 dormant_summary 字段才算有效推演
        if not result.get("dormant_summary"):
            logger.warning("Dormant wake LLM returned invalid for %s (no summary), degrading", npc.name)
            return self._rule_based_wake(npc, world_state, dormant_days)

        # 应用状态变化
        state_changes = result.get("state_changes", {}) or {}
        self._apply_state_changes(npc, state_changes, dormant_days)

        # 应用目标变化
        goal_changes = result.get("goal_changes", {}) or {}
        self._apply_goal_changes(npc, goal_changes, world_state)

        # 应用位置变化
        new_loc = (result.get("new_location") or "").strip()
        if new_loc and engine:
            resolved = self._resolve_location(new_loc, world_state)
            if resolved:
                npc.current_location = resolved

        # 写入行动记录（休眠摘要）
        dormant_summary = (result.get("dormant_summary") or "").strip()
        npc.recent_actions.append({
            "day": world_state.current_day,
            "action": "dormant_wake",
            "detail": f"休眠 {dormant_days} 天后苏醒：{dormant_summary}",
        })
        if len(npc.recent_actions) > 10:
            npc.recent_actions = npc.recent_actions[-10:]

        # 应用年龄增长
        if age_growth > 0:
            npc.age += age_growth

        logger.info(
            "[DormantWake] %s 休眠 %d 天后苏醒，推演完成：%s",
            npc.name, dormant_days, dormant_summary[:80],
        )

        return {
            "evaluated": True,
            "dormant_days": dormant_days,
            "dormant_summary": dormant_summary,
            "state_changes": state_changes,
            "personality_shift": result.get("personality_shift", ""),
            "goal_changes": goal_changes,
            "new_location": new_loc,
            "rumors_heard": result.get("rumors_heard", []),
            "degraded": False,
        }

    # ===== 规则降级（无 LLM 或短休眠）=====

    def _rule_based_wake(
        self,
        npc: "NPCState",
        world_state: "WorldState",
        dormant_days: int,
    ) -> dict:
        """规则驱动的轻量唤醒推演（不调 LLM）。

        适用场景：
          - 休眠天数 < LLM_THRESHOLD_DAYS（短休眠）
          - LLM 不可用
          - LLM 调用失败
        """
        changes = []

        # 体力/血量自然恢复（每天 +5，上限 100）
        if hasattr(npc.stats, "energy"):
            recovered = min(dormant_days * 5, 100 - npc.stats.energy)
            if recovered > 0:
                npc.stats.energy = min(100, npc.stats.energy + recovered)
                changes.append(f"体力恢复 {recovered}")
        if hasattr(npc.stats, "health"):
            # 受伤状态恢复更慢
            recover_rate = 3 if any(s in (npc.status_effects or [])
                                    for s in ("受伤", "重伤", "垂死")) else 5
            recovered = min(dormant_days * recover_rate, 100 - npc.stats.health)
            if recovered > 0:
                npc.stats.health = min(100, npc.stats.health + recovered)
                changes.append(f"血量恢复 {recovered}")
                # 伤愈后移除受伤状态
                if npc.stats.health >= 80:
                    npc.status_effects = [s for s in (npc.status_effects or [])
                                          if s not in ("受伤", "重伤")]
                    changes.append("伤势痊愈")

        # 年龄增长
        age_growth = dormant_days // 365
        if age_growth > 0:
            npc.age += age_growth
            changes.append(f"年龄 +{age_growth}")

        # 30% 概率小幅财富变化（幕后营生）
        if random.random() < 0.3 and hasattr(npc.stats, "gold"):
            delta = random.randint(-5, 10) * dormant_days
            npc.stats.gold = max(0, npc.stats.gold + delta)
            changes.append(f"财富 {'+' if delta >= 0 else ''}{delta}")

        # 写入行动记录
        summary = "休眠期间" + ("；".join(changes) if changes else "平静度日")
        npc.recent_actions.append({
            "day": world_state.current_day,
            "action": "dormant_wake",
            "detail": f"休眠 {dormant_days} 天后苏醒：{summary}",
        })
        if len(npc.recent_actions) > 10:
            npc.recent_actions = npc.recent_actions[-10:]

        logger.debug(
            "[DormantWake] %s 短休眠 %d 天，规则降级：%s",
            npc.name, dormant_days, summary,
        )

        return {
            "evaluated": True,
            "dormant_days": dormant_days,
            "dormant_summary": summary,
            "state_changes": {},
            "personality_shift": "",
            "goal_changes": {},
            "new_location": "",
            "rumors_heard": [],
            "degraded": True,
        }

    # ===== 工具方法 =====

    def _calc_dormant_days(self, npc: "NPCState", world_state: "WorldState") -> int:
        """计算休眠天数"""
        since = getattr(npc, "dormant_since_day", 0)
        if since <= 0:
            # 没记录休眠开始日，返回 0 表示不推演
            return 0
        current = world_state.current_day
        return max(0, current - since)

    def _collect_world_events(
        self,
        dormant_days: int,
        world_state: "WorldState",
        engine: "GameEngine | None",
    ) -> str:
        """收集休眠期间的世界大事"""
        if not engine:
            return ""
        try:
            # 从 narrative_history 提取休眠期间的事件
            since_day = world_state.current_day - dormant_days
            events = []
            for entry in (engine.narrative_history or []):
                entry_day = entry.get("day", 0) if isinstance(entry, dict) else 0
                if entry_day >= since_day and entry_day <= world_state.current_day:
                    text = entry.get("text", "") if isinstance(entry, dict) else str(entry)
                    if text:
                        events.append(f"第{entry_day}天: {text[:100]}")
            return "\n".join(events[:8]) or "（无重大事件记录）"
        except Exception:
            return "（无法获取世界事件）"

    def _apply_state_changes(
        self,
        npc: "NPCState",
        changes: dict,
        dormant_days: int,
    ):
        """应用 LLM 输出的状态变化"""
        stats = getattr(npc, "stats", None)
        if stats is None:
            return

        # 数值变化
        if "energy_delta" in changes:
            delta = int(changes["energy_delta"] or 0)
            npc.stats.energy = max(0, min(100, npc.stats.energy + delta))
        if "health_delta" in changes:
            delta = int(changes["health_delta"] or 0)
            npc.stats.health = max(0, min(100, npc.stats.health + delta))
        if "gold_delta" in changes:
            delta = int(changes["gold_delta"] or 0)
            if hasattr(npc.stats, "gold"):
                npc.stats.gold = max(0, npc.stats.gold + delta)

        # 标签增删
        tags_add = changes.get("tags_add", []) or []
        tags_remove = changes.get("tags_remove", []) or []
        existing_tags = list(npc.tags or [])
        for t in tags_add:
            if t and t not in existing_tags:
                existing_tags.append(t)
        for t in tags_remove:
            if t in existing_tags:
                existing_tags.remove(t)
        try:
            npc.tags = existing_tags
        except (AttributeError, TypeError):
            pass

        # 状态效果
        status_add = changes.get("status_add", []) or []
        if status_add:
            existing_status = list(npc.status_effects or [])
            for s in status_add:
                if s and s not in existing_status:
                    existing_status.append(s)
            try:
                npc.status_effects = existing_status
            except (AttributeError, TypeError):
                pass

    def _apply_goal_changes(
        self,
        npc: "NPCState",
        goal_changes: dict,
        world_state: "WorldState",
    ):
        """应用 LLM 输出的目标变化"""
        if not goal_changes:
            return
        ai_behavior = npc.ai_behavior or {}

        new_long = (goal_changes.get("long_term_goal") or "").strip()
        new_short = (goal_changes.get("short_term_goal") or "").strip()

        if new_long:
            ai_behavior["long_term_goal"] = new_long
        if new_short:
            ai_behavior["current_goal"] = new_short
            short_goals = ai_behavior.get("short_term_goals", []) or []
            if new_short not in short_goals:
                short_goals.insert(0, new_short)
            ai_behavior["short_term_goals"] = short_goals

        if new_long or new_short:
            npc.ai_behavior = ai_behavior

    def _resolve_location(self, loc_str: str, world_state: "WorldState") -> str | None:
        """把 LLM 输出的地点名解析为 location code"""
        if not loc_str or not world_state:
            return None
        locations = getattr(world_state, "locations", None) or {}
        if not locations:
            return None
        # 精确匹配显示名
        for code, loc_data in locations.items():
            if isinstance(loc_data, dict):
                name = loc_data.get("location_name", loc_data.get("name", ""))
            elif hasattr(loc_data, "location_name"):
                name = loc_data.location_name or ""
            elif hasattr(loc_data, "name"):
                name = loc_data.name or ""
            else:
                name = str(loc_data)
            if name and name == loc_str:
                return code
        # 模糊匹配
        for code, loc_data in locations.items():
            if isinstance(loc_data, dict):
                name = loc_data.get("location_name", loc_data.get("name", ""))
            elif hasattr(loc_data, "location_name"):
                name = loc_data.location_name or ""
            elif hasattr(loc_data, "name"):
                name = loc_data.name or ""
            else:
                name = str(loc_data)
            if name and (loc_str in name or name in loc_str):
                return code
        # 直接当 code 用
        if loc_str in locations:
            return loc_str
        return None


# 全局单例
_global_evaluator: DormantWakeEvaluator | None = None


def get_dormant_wake_evaluator() -> DormantWakeEvaluator:
    """获取全局 DormantWakeEvaluator 单例"""
    global _global_evaluator
    if _global_evaluator is None:
        _global_evaluator = DormantWakeEvaluator()
    return _global_evaluator


def set_dormant_wake_evaluator(evaluator: DormantWakeEvaluator):
    """注入带 LLM 的 evaluator（由 game_engine 启动时调用）"""
    global _global_evaluator
    _global_evaluator = evaluator
