"""
[v1.2] NPC 即时反应引擎 — 同场 NPC 感知到事件后触发的即时反应。
[v1.6] 升级为规则+LLM混合反应：重要事件走LLM生成定制反应，普通事件保留纯规则。

设计原则：
  - 事件触发式思考（与时序轮询互补）
  - 视野隔离：通过 npc_perception 判定能否感知
  - 节流：每个 NPC 每天最多 2 次即时反应，避免连锁反应爆炸
  - 反应类型：围观/惊呼/助阵/劝阻/逃离/记录/无反应
  - [v1.6] 规则筛选 + LLM 兜底：玩家在场 / 核心NPC / 势力冲突走LLM

工作流：
  EventBus.on("on_npc_action") → 收到事件
    → 找到同场景/邻近场景的非行动者 NPC
    → 对每个能感知的 NPC：
        1. roll 反应概率（基于感知区 + 事件严重度）
        2. 决定反应类型（规则初筛 → 重要场景走 LLM）
        3. 应用反应效果（如逃离→移动；助阵→加入战斗）
        4. 写入 NPC 记忆
"""
from __future__ import annotations
import random
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schemas import NPCState, PlayerState, WorldState
    from .event_bus import EventBus
    from .npc_perception import NPCPerceptionSystem
    from .llm.base_llm import BaseLLM

logger = logging.getLogger("chronoverse.reaction")


# ===== [v1.6] LLM 反应触发条件 =====
# 满足任一条件即走 LLM 生成定制反应（其余保留纯规则）
def _should_use_llm_reaction(
    npc: "NPCState",
    event_data: dict,
    zone: str,
    player: "PlayerState | None",
) -> bool:
    """判定是否对该事件使用 LLM 生成反应。

    规则：
    - sleeping/aware 区不调 LLM（远处只需规则反应）
    - active 区 + 玩家在场 → LLM（玩家会看到反应）
    - active 区 + critical 事件 → LLM（重大时刻需要细腻反应）
    - active 区 + 核心NPC（tag 含「重要」「剧情」「主角相关」）→ LLM
    - 势力冲突事件（action_type 涉及刺杀/挑战/比武）→ LLM
    """
    if zone != "active":
        return False

    severity = event_data.get("severity", "normal")
    action_type = event_data.get("action_type", "")

    # 玩家在场
    if player is not None:
        player_loc = getattr(player, "location", "") or ""
        event_loc = event_data.get("location", "") or ""
        if player_loc and event_loc and player_loc == event_loc:
            return True

    # 严重事件
    if severity in ("critical", "major"):
        return True

    # 势力冲突
    if action_type in ("刺杀", "挑战", "比武", "强闯", "伏击", "胁迫"):
        return True

    # 核心 NPC
    tags = getattr(npc, "tags", None) or []
    if any(t in tags for t in ("重要", "剧情", "主角相关")):
        return True

    return False


# ===== [v1.6] LLM 反应 prompt =====
LLM_REACTION_PROMPT = """你是虚拟世界中 NPC 的反应决策器。请根据事件和 NPC 人设，生成一个自然、有深度的反应。

【事件信息】
事件类型：{action_type}
严重度：{severity}
行动者：{actor_name}
事件摘要：{summary}
发生地点：{location}

【NPC 信息】
姓名：{npc_name}
年龄：{npc_age}
身份：{npc_role}
性格：{personality}
说话风格：{speaking_style}
立场：{alignment}
对行动者好感：{favor}/100

【场景背景】
{scene_context}

【输出 JSON 格式】
{{
    "reaction_type": "flee/warn/help/oppose/watch/record/greet/join/interrupt/ignore 之一",
    "dialogue": "NPC 说的话（50字以内，可为空字符串）",
    "action": "NPC 的动作描写（30字以内，可为空字符串）",
    "emotion": "情绪关键词（如愤怒/恐惧/好奇/冷漠）",
    "favor_change": -5到5的整数（对行动者好感变化）,
    "memory_note": "NPC 内心的想法（30字以内，会写入记忆）"
}}
只输出 JSON。"""


# ===== 反应概率表 =====
# (感知区, 事件严重度) → 反应概率
REACTION_PROBABILITY = {
    ("active", "critical"): 0.95,  # 同场景大事件几乎必反应
    ("active", "major"):     0.80,
    ("active", "normal"):    0.50,
    ("active", "minor"):     0.20,
    ("aware", "critical"):   0.60,
    ("aware", "major"):      0.40,
    ("aware", "normal"):     0.20,
    ("aware", "minor"):      0.05,
    ("rumor", "critical"):   0.30,  # 远处大事件可能听闻
    ("rumor", "major"):      0.15,
    ("rumor", "normal"):     0.05,
    ("rumor", "minor"):      0.00,
    ("sleeping", "critical"): 0.10,
    ("sleeping", "major"):    0.00,
    ("sleeping", "normal"):   0.00,
    ("sleeping", "minor"):    0.00,
}


# ===== 反应类型与效果 =====
REACTION_TYPES = {
    # 严重事件（刺杀/挑战）
    "flee":      {"energy_cost": 10, "tag_add": ["惊慌"]},        # 逃离现场
    "warn":      {"energy_cost": 5},                               # 大声示警
    "help":      {"energy_cost": 15, "favor_player": 5},           # 助阵玩家方
    "oppose":    {"energy_cost": 15, "favor_player": -5},          # 助阵对方
    "watch":     {"energy_cost": 2},                               # 围观
    "record":    {"energy_cost": 1},                               # 默记于心
    "ignore":    {"energy_cost": 0},                               # 无反应
    # 社交事件
    "greet":     {"energy_cost": 2, "favor_player": 1},
    "join":      {"energy_cost": 5},                               # 加入对话
    "interrupt": {"energy_cost": 5},                               # 插嘴
}


class NpcReactionEngine:
    """NPC 即时反应引擎

    用法：
        engine = NpcReactionEngine(perception, world_state, all_npcs)
        engine.bind_to_event_bus(eng.event_bus)

    [v1.6] 支持注入 LLM 实现规则+LLM混合反应：
        engine = NpcReactionEngine(perception=..., llm=...)
        重要事件走 LLM 生成定制反应（含 dialogue/action/emotion），
        普通事件保留纯规则快速决策。
    """

    # 每个 NPC 每天最多反应次数（避免连锁爆炸）
    MAX_REACTIONS_PER_DAY = 2
    # [v1.6] 每个 NPC 每天最多 LLM 反应次数（控制成本）
    MAX_LLM_REACTIONS_PER_DAY = 1

    def __init__(
        self,
        perception: "NPCPerceptionSystem | None" = None,
        llm: "BaseLLM | None" = None,
    ):
        self.perception = perception
        self.llm = llm  # [v1.6] 可选 LLM，启用后重要事件走 LLM
        # 节流计数：{npc_id: {day: count}}
        self._reaction_counts: dict[str, dict[int, int]] = {}
        # [v1.6] LLM 反应节流：{npc_id: {day: count}}
        self._llm_reaction_counts: dict[str, dict[int, int]] = {}

    def bind_to_event_bus(self, bus: "EventBus"):
        """绑定到 EventBus，监听 on_npc_action 事件"""
        bus.on("on_npc_action", self._on_npc_action, priority=50,
               plugin_name="npc_reaction")
        logger.info("[NpcReaction] 已绑定 EventBus，监听 on_npc_action")

    def _on_npc_action(self, event_data: dict):
        """EventBus 回调：处理 NPC 行动事件"""
        try:
            self.handle_action_event(event_data)
        except Exception as e:
            logger.warning("[NpcReaction] handle action event failed: %s", e)

    def handle_action_event(
        self,
        event_data: dict,
        all_npcs: dict = None,
        world_state: "WorldState" = None,
        player: "PlayerState" = None,
    ) -> list[dict]:
        """处理一个 NPC 行动事件，触发同场 NPC 的即时反应。

        Args:
            event_data: EventBus 事件数据
                        必需字段: actor_id, action_type, location, severity, summary
            all_npcs: 全部 NPC 字典（若 None 则用注入的 _all_npcs）
            world_state: 世界状态
            player: 玩家状态

        Returns:
            反应记录列表
        """
        # 从注入的上下文取（game_engine 启动时注入）
        if all_npcs is None:
            all_npcs = getattr(self, "_all_npcs", {}) or {}
        if world_state is None:
            world_state = getattr(self, "_world_state", None)
        if player is None:
            player = getattr(self, "_player", None)

        if not all_npcs or not world_state:
            return []

        actor_id = event_data.get("actor_id", "")
        action_type = event_data.get("action_type", "")
        location = event_data.get("location", "")
        severity = event_data.get("severity", "normal")
        summary = event_data.get("summary", "")
        day = event_data.get("day", world_state.current_day)

        # 若是重大事件，提升一档严重度（便于触发反应）
        if action_type in ("刺杀", "挑战", "比武") and severity == "normal":
            severity = "major"

        reactions = []

        for npc_id, npc in all_npcs.items():
            # 跳过行动者自己
            if npc_id == actor_id:
                continue
            # 跳过休眠/已故/昏迷
            if getattr(npc, "is_dormant", False):
                continue
            if "已故" in (npc.tags or []):
                continue
            if any(s in (npc.status_effects or []) for s in ("昏迷", "垂死", "囚禁")):
                continue
            # 跳过玩家（玩家不通过此机制反应）
            if npc_id == "player":
                continue

            # 节流检查
            if not self._can_react(npc_id, day):
                continue

            # 感知判定
            zone = self._get_zone(npc_id, npc, location, world_state, player)
            prob = REACTION_PROBABILITY.get((zone, severity), 0.0)
            if random.random() > prob:
                continue

            # [v1.6] 决定是否走 LLM 路径
            use_llm = (
                self.llm is not None
                and _should_use_llm_reaction(npc, event_data, zone, player)
                and self._can_llm_react(npc_id, day)
            )

            if use_llm:
                # LLM 路径：生成完整反应对象
                llm_result = self._decide_reaction_llm(
                    npc, event_data, zone, world_state, player,
                )
                if llm_result is None:
                    # LLM 失败 → 回退到规则
                    reaction_type = self._decide_reaction(
                        npc, action_type, severity, zone, world_state, player,
                    )
                    llm_result = None
                else:
                    reaction_type = llm_result.get("reaction_type", "watch")
                    self._incr_llm_react_count(npc_id, day)
            else:
                # 规则路径
                reaction_type = self._decide_reaction(
                    npc, action_type, severity, zone, world_state, player,
                )
                llm_result = None

            if reaction_type == "ignore":
                continue

            # 应用反应效果
            effect_summary = self._apply_reaction(npc, reaction_type, day, world_state)

            # [v1.6] 如果 LLM 提供了对话/动作/情绪，合并到反应记录
            dialogue = ""
            action_desc = ""
            emotion = ""
            memory_note = ""
            favor_change = 0
            if llm_result:
                dialogue = llm_result.get("dialogue", "") or ""
                action_desc = llm_result.get("action", "") or ""
                emotion = llm_result.get("emotion", "") or ""
                memory_note = llm_result.get("memory_note", "") or ""
                try:
                    favor_change = int(llm_result.get("favor_change", 0) or 0)
                except (TypeError, ValueError):
                    favor_change = 0
                # 好感变化应用到 NPC 对行动者的关系
                if favor_change != 0 and actor_id and actor_id != "player":
                    self._adjust_npc_favor(npc, actor_id, favor_change, all_npcs)

            # 写入记忆（含 LLM 提供的想法）
            memory_detail = f"目睹{summary}，反应：{reaction_type}"
            if memory_note:
                memory_detail += f"（心念：{memory_note}）"
            npc.recent_actions.append({
                "day": day,
                "action": "react",
                "detail": memory_detail,
                "dialogue": dialogue,
                "emotion": emotion,
            })
            if len(npc.recent_actions) > 10:
                npc.recent_actions = npc.recent_actions[-10:]

            # 计数
            self._incr_react_count(npc_id, day)

            reactions.append({
                "npc_id": npc_id,
                "npc_name": npc.name,
                "reaction": reaction_type,
                "to_event": action_type,
                "actor": event_data.get("actor_name", ""),
                "summary": effect_summary,
                "day": day,
                # [v1.6] LLM 反应的附加字段（普通规则反应时为空）
                "dialogue": dialogue,
                "action_desc": action_desc,
                "emotion": emotion,
                "llm_generated": bool(llm_result),
            })

            logger.debug(
                "[NpcReaction] %s 目睹 %s 的「%s」事件，反应：%s%s",
                npc.name, event_data.get("actor_name", ""), action_type, reaction_type,
                f" (LLM: {dialogue[:30]}...)" if dialogue else "",
            )

        return reactions

    # ===== 工具方法 =====

    def _get_zone(self, npc_id: str, npc: "NPCState", event_location: str,
                   world_state: "WorldState", player: "PlayerState") -> str:
        """获取 NPC 相对事件地点的感知区"""
        if self.perception:
            # 临时把 player.location 当成事件地点来分类
            try:
                # 简化：直接比较 NPC 位置和事件位置
                npc_loc = npc.current_location or ""
                if not event_location:
                    return "rumor"
                if npc_loc == event_location:
                    return "active"
                # 同区域判定
                if self.perception._same_area(npc_loc, event_location):
                    return "aware"
                return "rumor"
            except Exception:
                pass

        # 降级：用距离字符串粗略判断
        npc_loc = npc.current_location or ""
        if not event_location:
            return "rumor"
        if npc_loc == event_location:
            return "active"
        # 共同前缀
        if any(p in npc_loc for p in event_location.split("/") if p):
            return "aware"
        return "rumor"

    def _can_react(self, npc_id: str, day: int) -> bool:
        """节流：每个 NPC 每天最多 N 次反应"""
        counts = self._reaction_counts.setdefault(npc_id, {})
        return counts.get(day, 0) < self.MAX_REACTIONS_PER_DAY

    def _incr_react_count(self, npc_id: str, day: int):
        counts = self._reaction_counts.setdefault(npc_id, {})
        counts[day] = counts.get(day, 0) + 1
        # 清理旧数据（保留近 7 天）
        for old_day in list(counts.keys()):
            if day - old_day > 7:
                del counts[old_day]

    # [v1.6] LLM 反应节流
    def _can_llm_react(self, npc_id: str, day: int) -> bool:
        """LLM 反应节流：每个 NPC 每天最多 1 次 LLM 反应"""
        counts = self._llm_reaction_counts.setdefault(npc_id, {})
        return counts.get(day, 0) < self.MAX_LLM_REACTIONS_PER_DAY

    def _incr_llm_react_count(self, npc_id: str, day: int):
        counts = self._llm_reaction_counts.setdefault(npc_id, {})
        counts[day] = counts.get(day, 0) + 1
        for old_day in list(counts.keys()):
            if day - old_day > 7:
                del counts[old_day]

    # [v1.6] LLM 反应决策
    def _decide_reaction_llm(
        self,
        npc: "NPCState",
        event_data: dict,
        zone: str,
        world_state: "WorldState",
        player: "PlayerState | None",
    ) -> "dict | None":
        """调 LLM 生成完整的反应对象。

        Returns:
            {reaction_type, dialogue, action, emotion, favor_change, memory_note}
            失败返回 None（调用方回退到规则）
        """
        if not self.llm:
            return None

        # 获取行动者好感
        actor_id = event_data.get("actor_id", "")
        actor_name = event_data.get("actor_name", "")
        favor = 50
        if actor_id and actor_id != "player":
            # 从 NPC 的 relations 里查
            relations = getattr(npc, "relations", None) or {}
            if actor_id in relations:
                favor = relations[actor_id].favor
            else:
                # 没有直接关系记录，从 social_network 取
                try:
                    sn = getattr(self, "_social_network", None)
                    if sn:
                        s = sn.get_relation_strength(npc.agent_id, actor_id)
                        if s > 0:
                            favor = s
                except Exception:
                    pass

        # 场景上下文
        scene_context_parts = []
        if world_state:
            scene_context_parts.append(f"第{world_state.current_day}天 {world_state.current_time}，{world_state.weather}")
            if world_state.event_history_summary:
                scene_context_parts.append(f"近期大事：{world_state.event_history_summary[-200:]}")
        scene_context = "；".join(scene_context_parts) or "无特殊背景"

        # 地点显示名
        location = event_data.get("location", "")
        location_display = location
        if world_state and hasattr(world_state, "locations") and location in world_state.locations:
            loc_obj = world_state.locations[location]
            if isinstance(loc_obj, dict):
                location_display = loc_obj.get("location_name") or loc_obj.get("name") or location
            elif hasattr(loc_obj, "location_name"):
                location_display = loc_obj.location_name or location

        try:
            prompt = LLM_REACTION_PROMPT.format(
                action_type=event_data.get("action_type", ""),
                severity=event_data.get("severity", "normal"),
                actor_name=actor_name or "未知",
                summary=event_data.get("summary", "")[:200],
                location=location_display,
                npc_name=npc.name,
                npc_age=npc.age,
                npc_role=npc.role or "无",
                personality=npc.personality or "普通",
                speaking_style=npc.speaking_style or "正常",
                alignment=getattr(npc, "alignment", "中庸"),
                favor=favor,
                scene_context=scene_context,
            )
            result = self.llm.chat_json(prompt, temperature=0.7, max_tokens=0)
            if not result or "reaction_type" not in result:
                return None
            # 校验 reaction_type
            valid_types = set(REACTION_TYPES.keys()) | {"ignore"}
            if result["reaction_type"] not in valid_types:
                result["reaction_type"] = "watch"
            return result
        except Exception as e:
            logger.debug("[NpcReaction] LLM 反应失败 (%s): %s", npc.name, e)
            return None

    def _adjust_npc_favor(
        self,
        npc: "NPCState",
        target_id: str,
        change: int,
        all_npcs: dict,
    ):
        """[v1.6] 调整 NPC 对目标的好感度。

        优先更新 npc.relations，回退到 social_network。
        """
        if not target_id or target_id == "player":
            return
        try:
            relations = getattr(npc, "relations", None)
            if relations is not None and target_id in relations:
                old = relations[target_id].favor
                relations[target_id].favor = max(-100, min(100, old + change))
                return
            # 回退到 social_network
            sn = getattr(self, "_social_network", None)
            if sn:
                cur = sn.get_relation_strength(npc.agent_id, target_id)
                new_strength = max(0, min(100, (cur or 50) + change))
                sn.add_link(
                    npc.agent_id, target_id,
                    "熟人" if new_strength >= 30 else "点头之交",
                    strength=new_strength,
                )
        except Exception as e:
            logger.debug("[NpcReaction] 好感更新失败: %s", e)

    def _decide_reaction(self, npc: "NPCState", action_type: str,
                          severity: str, zone: str,
                          world_state: "WorldState",
                          player: "PlayerState | None") -> str:
        """决定 NPC 的反应类型。

        优先用规则（性格+立场+关系），复杂情况才用 LLM。
        本版本纯规则，避免 LLM 调用爆炸。
        """
        # 暴力类事件
        if action_type in ("刺杀", "挑战", "比武", "强闯", "伏击", "胁迫"):
            # 性格倾向
            personality = npc.personality or ""
            alignment = getattr(npc, "alignment", "中庸")

            # 善良/正义倾向 → 示警或助阵弱者
            if any(k in personality for k in ("侠", "义", "善", "正")) or alignment in ("仁善", "刚正"):
                if severity in ("critical", "major"):
                    return random.choice(["warn", "help"])
                return "warn"

            # 邪恶/桀骜 → 助阵加害方或围观
            if any(k in personality for k in ("邪", "狂", "魔")) or alignment in ("狂邪", "唯我"):
                return random.choice(["oppose", "watch"])

            # 胆小/谨慎 → 逃离
            if any(k in personality for k in ("怯", "谨慎", "保守")):
                return "flee" if severity in ("critical", "major") else "watch"

            # 默认：围观
            return "watch"

        # 偷窃类事件
        if action_type in ("偷窃", "潜行", "暗杀"):
            # 大概率无视（没看见），小概率察觉
            if zone == "active" and severity in ("critical", "major"):
                return random.choice(["warn", "flee"])
            return "ignore"

        # 社交类事件
        if action_type in ("拜访", "邀约", "赠送", "交谈"):
            # 同场景友好 NPC 可能加入
            if zone == "active":
                personality = npc.personality or ""
                if any(k in personality for k in ("热", "豪爽", "外向")):
                    return random.choice(["greet", "join"])
            return "ignore"

        # 默认
        return "watch" if severity in ("critical", "major") else "ignore"

    def _apply_reaction(self, npc: "NPCState", reaction_type: str,
                         day: int, world_state: "WorldState") -> str:
        """应用反应效果到 NPC"""
        config = REACTION_TYPES.get(reaction_type, {})
        energy_cost = config.get("energy_cost", 0)

        # 消耗体力
        if energy_cost > 0:
            npc.stats.energy = max(0, npc.stats.energy - energy_cost)

        # 加 tag
        for tag in config.get("tag_add", []):
            if tag not in (npc.tags or []):
                npc.tags.append(tag)

        # 好感变化（仅在与玩家相关的事件中）
        favor_change = config.get("favor_player", 0)
        if favor_change and hasattr(npc, "relation_to_player"):
            npc.relation_to_player.favor = max(
                -100, min(100, npc.relation_to_player.favor + favor_change)
            )

        # 逃离：随机移动到附近地点
        if reaction_type == "flee":
            # 简化：标记位置变化（实际位置变更由后续 npc_agent 决定）
            npc.status_effects = list(npc.status_effects or []) + ["惊慌"]

        # 反应摘要
        return f"{npc.name}的反应：{reaction_type}（体力-{energy_cost}）"


# 全局单例
_global_engine: NpcReactionEngine | None = None


def get_npc_reaction_engine() -> NpcReactionEngine:
    global _global_engine
    if _global_engine is None:
        _global_engine = NpcReactionEngine()
    return _global_engine


def set_npc_reaction_engine(engine: NpcReactionEngine):
    global _global_engine
    _global_engine = engine
