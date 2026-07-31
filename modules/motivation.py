"""
[v1.5 第二期] 6 类动机触发器

设计要点：
  1. 6 类动机：survival/social/career/exploration/legacy/transcendence
  2. 触发条件 = 状态判定 + 随机抽取（不调 LLM）
  3. 动机强度 0-100，每天衰减 ×0.7，强度 < 20 时移除
  4. WorldTick 跨日时 roll 动机 → 根据动机类型选事件模板
  5. 适配多种世界类型（修仙/魔法/古代/未来...）：transcendence 仅在修仙/魔法世界激活

动机类型说明：
  - survival      生存：HP/体力低、饥饿、伤病 → 求医/求助/觅食
  - social        社交：孤独、好感高、喜事/丧事 → 拜访/送礼/邀约
  - career        事业：缺钱、有目标、地位低 → 合作/接任务/求教
  - exploration   探索：好奇心、有消息、年轻 → 邀你外出/通报发现
  - legacy        传承：年老、有子女/师承、将死 → 收徒/托付/传授
  - transcendence 超越：修仙/魔法世界、瓶颈期 → 切磋/论道/共修

数据结构（NPCState.motivations）：
  [
    {
      "type": "survival",
      "intensity": 75,          # 0-100
      "target": "player",        # 目标对象（player/npc_id/location/faction_id）
      "triggered_day": 42,       # 触发日
      "decay_rate": 0.7,         # 每日衰减率
      "reason": "重伤未愈"       # 触发原因（用于桥接叙事）
    },
    ...
  ]
"""
from __future__ import annotations
import random
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schemas import NPCState, PlayerState, WorldState

logger = logging.getLogger("chronoverse.motivation")


# ===== 6 类动机定义 =====

MOTIVATION_TYPES = (
    "survival",       # 生存
    "social",         # 社交
    "career",         # 事业
    "exploration",    # 探索
    "legacy",         # 传承
    "transcendence",  # 超越
)

# 每类动机对应的玩家事件模板（扩展第一期 PLAYER_EVENT_TEMPLATES）
# 字段：(event_type, priority, summary_template)
MOTIVATION_EVENT_TEMPLATES = {
    "survival": [
        ("ask_help",   "urgent",    "{name}神色焦急，似乎受了伤需要救助"),
        ("deliver_msg","urgent",    "{name}急匆匆赶来，说有性命之忧的事相告"),
    ],
    "social": [
        ("visit",      "important", "{name}前来拜访，说有要事相商"),
        ("invite",     "important", "{name}邀你一同外出，似乎有什么打算"),
        ("gift",       "normal",    "{name}带了礼物前来，说是心意"),
    ],
    "career": [
        ("invite",     "important", "{name}邀你合作，似乎有个赚钱的机会"),
        ("ask_help",   "important", "{name}有事相求，可能是工作上的"),
    ],
    "exploration": [
        ("visit",      "normal",    "{name}兴冲冲跑来，说发现了一处新地方"),
        ("deliver_msg","normal",    "{name}带来一个消息，特意跑来相告"),
    ],
    "legacy": [
        ("visit",      "important", "{name}前来托付后事，神情庄重"),
        ("gift",       "important", "{name}带来一份厚礼，似有传承之意"),
    ],
    "transcendence": [
        ("invite",     "important", "{name}邀你论道切磋，似有突破之机"),
        ("visit",      "normal",    "{name}前来交流心得，互证所学"),
    ],
}

# 修仙/魔法世界类型（transcendence 动机仅在这些世界激活）
TRANSCENDENCE_WORLD_TYPES = {
    "xianxia", "fantasy", "wuxia", "urban_fantasy", "custom",
}

# 衰减率和移除阈值
DECAY_RATE = 0.7
REMOVE_THRESHOLD = 20


class MotivationEngine:
    """6 类动机触发器

    使用方式：
        engine = MotivationEngine()
        motivation = engine.roll_motivation(npc, world_state, player)
        if motivation:
            npc.motivations.append(motivation)
    """

    def roll_motivation(self, npc: "NPCState", world_state: "WorldState",
                        player: "PlayerState" = None) -> dict | None:
        """为 NPC roll 一个新动机

        优先级：
          1. 状态触发（HP 低 → survival；年老 → legacy；...）
          2. 关系触发（好感高 → social）
          3. 随机抽取剩余类型

        Returns:
            动机 dict 或 None（无可用动机）
        """
        candidates = self._get_candidate_motivations(npc, world_state, player)
        if not candidates:
            return None

        # 按权重随机选一个
        weights = [c["weight"] for c in candidates]
        chosen = random.choices(candidates, weights=weights, k=1)[0]

        return {
            "type": chosen["type"],
            "intensity": chosen["intensity"],
            "target": chosen.get("target", "player"),
            "triggered_day": world_state.current_day,
            "decay_rate": DECAY_RATE,
            "reason": chosen.get("reason", ""),
        }

    def _get_candidate_motivations(self, npc: "NPCState", world_state: "WorldState",
                                    player: "PlayerState") -> list[dict]:
        """根据 NPC 当前状态收集候选动机桶"""
        candidates: list[dict] = []
        day = world_state.current_day

        # ===== 1. survival 生存：HP/体力低、伤病 =====
        hp_ratio = npc.stats.health / max(npc.stats.max_health, 1)
        energy_ratio = npc.stats.energy / max(npc.stats.max_energy, 1)
        has_illness = any(s in (npc.status_effects or []) for s in ("重伤", "昏迷", "中毒", "生病", "病人"))
        if hp_ratio < 0.3 or energy_ratio < 0.2 or has_illness:
            intensity = min(100, int(60 + (1 - hp_ratio) * 40))
            candidates.append({
                "type": "survival", "intensity": intensity,
                "target": "player", "weight": 5.0,
                "reason": "伤病未愈" if has_illness else "体力不支",
            })

        # ===== 2. social 社交：好感高、有喜事/丧事 =====
        favor = getattr(npc.relation_to_player, "favor", 50)
        if favor >= 70:
            candidates.append({
                "type": "social", "intensity": min(80, 40 + favor // 2),
                "target": "player", "weight": 3.0,
                "reason": "故交来访",
            })
        elif 30 <= favor < 70 and random.random() < 0.3:
            candidates.append({
                "type": "social", "intensity": 40,
                "target": "player", "weight": 1.5,
                "reason": "顺道拜访",
            })

        # ===== 3. career 事业：缺钱、有目标、地位低 =====
        ai_behavior = npc.ai_behavior or {}
        current_goal = (ai_behavior.get("current_goal") or "").lower()
        has_career_goal = any(k in current_goal for k in ("赚钱", "工作", "事业", "地位", "升职", "money", "career"))
        if has_career_goal or random.random() < 0.2:
            candidates.append({
                "type": "career", "intensity": 50,
                "target": "player", "weight": 2.0,
                "reason": "有事相商",
            })

        # ===== 4. exploration 探索：年轻 + 性格含"好奇"/"冒险" =====
        age = npc.age or 20
        personality = npc.personality or ""
        if age <= 35 and any(k in personality for k in ("好奇", "冒险", "热血", "少年")):
            candidates.append({
                "type": "exploration", "intensity": 55,
                "target": "player", "weight": 2.0,
                "reason": "有所发现",
            })
        elif random.random() < 0.1:
            candidates.append({
                "type": "exploration", "intensity": 35,
                "target": "player", "weight": 1.0,
                "reason": "闲来无事",
            })

        # ===== 5. legacy 传承：年老、有子女/师承、将死 =====
        if age >= 55:
            has_children = any(t in (npc.tags or []) for t in ("为人父母", "父亲", "母亲"))
            has_apprentice = any(k in personality for k in ("师", "传授", "传人"))
            if has_children or has_apprentice or age >= 70:
                candidates.append({
                    "type": "legacy", "intensity": min(80, 50 + (age - 55) * 2),
                    "target": "player", "weight": 3.0,
                    "reason": "年事已高，欲托付后事",
                })

        # ===== 6. transcendence 超越：修仙/魔法世界、瓶颈期 =====
        world_type = getattr(world_state, "world_type", "historical")
        if world_type in TRANSCENDENCE_WORLD_TYPES:
            # 性格含"修"/"道"/"法"/"突破" 或随机
            has_cultivation_goal = any(
                k in current_goal
                for k in ("修", "道", "法", "突破", "瓶颈", "境界", "悟")
            ) or any(k in personality for k in ("修", "道", "法", "悟"))
            if has_cultivation_goal or random.random() < 0.15:
                candidates.append({
                    "type": "transcendence", "intensity": 50,
                    "target": "player", "weight": 2.5,
                    "reason": "瓶颈期，欲求切磋",
                })

        # 过滤掉 NPC 当前已存在的同类型动机（避免重复）
        existing_types = {m.get("type") for m in (npc.motivations or [])}
        candidates = [c for c in candidates if c["type"] not in existing_types]

        return candidates

    def decay_motivations(self, npc: "NPCState", current_day: int) -> int:
        """对 NPC 的所有动机执行衰减，移除强度过低的

        [v1.5 第二期 修复] 使用增量衰减：记录 last_decay_day，
        每次只衰减 (current_day - last_decay_day) 天，避免重复衰减。

        Returns:
            移除的动机数量
        """
        motivations = getattr(npc, "motivations", None)
        if not motivations:
            return 0

        removed = 0
        survived: list[dict] = []
        for m in motivations:
            # 上次衰减日（首次衰减时用 triggered_day）
            last_decay_day = m.get("last_decay_day", m.get("triggered_day", current_day))
            days_passed = max(0, current_day - last_decay_day)
            if days_passed > 0:
                # 增量衰减：每天 × decay_rate
                decay = m.get("decay_rate", DECAY_RATE) ** days_passed
                m["intensity"] = int(m.get("intensity", 50) * decay)
                m["last_decay_day"] = current_day
            # 无论是否衰减，都检查阈值
            if m["intensity"] >= REMOVE_THRESHOLD:
                survived.append(m)
            else:
                removed += 1
                logger.debug(
                    "NPC %s motivation %s decayed below threshold (intensity=%d, day=%d)",
                    getattr(npc, "name", "?"), m.get("type"), m["intensity"], current_day,
                )

        # 兼容 SimpleNamespace 等无 setattr 限制的对象
        try:
            npc.motivations = survived
        except (AttributeError, TypeError):
            pass
        return removed

    def pick_active_motivation(self, npc: "NPCState") -> dict | None:
        """挑出 NPC 当前强度最高的动机（用于决定事件模板）

        Returns:
            动机 dict 或 None（无活跃动机）
        """
        motivations = getattr(npc, "motivations", None)
        if not motivations:
            return None
        return max(motivations, key=lambda m: m.get("intensity", 0))

    def pick_event_template(self, motivation_type: str) -> tuple[str, str, str]:
        """根据动机类型挑选事件模板

        Returns:
            (event_type, priority, summary_template)
        """
        templates = MOTIVATION_EVENT_TEMPLATES.get(motivation_type)
        if not templates:
            # 回退到中性社交模板
            return ("visit", "normal", "{name}前来拜访")
        return random.choice(templates)


# 全局单例（WorldTick 复用）
_global_engine: MotivationEngine | None = None


def get_motivation_engine() -> MotivationEngine:
    """获取全局 MotivationEngine 单例"""
    global _global_engine
    if _global_engine is None:
        _global_engine = MotivationEngine()
    return _global_engine
