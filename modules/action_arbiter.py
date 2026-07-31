"""
[v1.2] NPC 行动裁决层 — 过滤 AI 空想，防止剧情崩坏。

设计原则（项目核心准则："代码管逻辑，LLM管叙事"）：
  NPC 行动经 BranchPlanner/NpcAutonomous 输出后，必须经裁决层判定能否执行：
    1. 可行性判定：移动距离、目标地点存在性、所需物品/资源
    2. 对抗判定：刺杀/偷窃/挑战等走 dice_engine
    3. 后果生成：成功/失败/部分成功的具体状态变化（程序决定，不交给 LLM）
    4. 事件广播：经裁定后产生 ActionVerdict，emit 到 EventBus 供在场 NPC 反应

不引入新概念：
  - 复用 NPCState.stats / tags / status_effects
  - 复用 world_state.known_locations（已知地点列表）
  - 复用 dice_engine 做对抗判定
  - 复用 EventBus 做事件广播
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .dice_engine import get_dice_engine, DiceResult

if TYPE_CHECKING:
    from .schemas import NPCState, PlayerState, WorldState
    from .event_bus import EventBus

logger = logging.getLogger("chronoverse.arbiter")


# ===== 行动类型分类 =====
# 对抗性：需要走 dice_roll
COMBAT_ACTIONS = {"刺杀", "挑战", "比武", "强闯", "胁迫", "伏击"}
STEALTH_ACTIONS = {"偷窃", "潜行", "追踪", "逃脱", "暗杀"}
SOCIAL_ACTIONS = {"说服", "辩论", "欺骗", "威胁", "诱惑"}
# 非对抗性：仅做可行性判定
MOVE_ACTIONS = {"移动", "前往", "赶路", "离开", "撤退"}
SOLO_ACTIONS = {"修炼", "采药", "读书", "休息", "练功", "冥想", "整理"}
SOCIAL_FRIENDLY = {"拜访", "邀约", "赠送", "交谈", "传递消息"}

# [v1.2] 谋划行为（暗中布局/传讯）：新增第 7 类行动
# 特征：
#   - 非直接对抗，但可能涉及智力对抗（用 dice 判定"谋划成败"）
#   - 广播半径小（隐秘行为，仅同伙/被识破时暴露）
#   - 失败可能反噬（被识破→lose favor，甚至触发 tag "被识破"）
#   - 包含传讯类：通过中间人传话/送信
SCHEME_ACTIONS = {
    # 暗中布局类
    "暗中布局", "设局", "算计", "收买", "离间", "布局", "谋划", "暗中打探",
    # 传讯类（托人传话/送信，区别于「传递消息」的友好社交）
    "托人传递消息", "传话", "送信", "飞鸽传书", "飞剑传讯",
}


@dataclass
class ActionVerdict:
    """行动裁决结果"""
    action_type: str              # 行动类型
    actor_id: str                 # 行动者 NPC id
    actor_name: str               # 行动者名字
    target_id: str = ""           # 目标 id（对抗/社交类）
    target_name: str = ""         # 目标名字
    location: str = ""            # 行动发生地
    day: int = 0                  # 行动日

    # 裁决结果
    feasible: bool = True         # 是否可行（不违反世界规则）
    feasible_reason: str = ""     # 不可行理由
    executed: bool = False        # 是否实际执行（feasible=True 且 dice 通过）
    dice_result: DiceResult | None = None  # 对抗判定结果

    # 状态变化（程序决定，已应用到 actor/target）
    actor_effects: dict = field(default_factory=dict)
    target_effects: dict = field(default_factory=dict)

    # 叙事素材（供章节生成 LLM 引用）
    narrative_summary: str = ""   # 一句话叙事
    severity: str = "normal"      # minor/normal/major/critical

    # 是否需要广播给在场 NPC
    should_broadcast: bool = True
    broadcast_radius: int = 2     # 同场景 + 邻近场景


class ActionArbiter:
    """NPC 行动裁决层

    用法：
        arbiter = ActionArbiter(event_bus=eng.event_bus)
        verdict = arbiter.adjudicate(action_dict, actor_npc, world_state, all_npcs, player)
        if verdict.executed:
            # 应用状态变化已自动完成
            pass
    """

    def __init__(self, event_bus: "EventBus | None" = None):
        self.event_bus = event_bus
        self.dice = get_dice_engine()

    def adjudicate(
        self,
        action: dict,
        actor: "NPCState",
        world_state: "WorldState",
        all_npcs: dict | None = None,
        player: "PlayerState | None" = None,
    ) -> ActionVerdict:
        """裁决一个 NPC 行动。

        Args:
            action: BranchPlanner/NpcAutonomous 输出的行动 dict
                    期望字段: action_type, target_name/target_id, location, detail
            actor: 行动 NPC
            world_state: 世界状态
            all_npcs: 全部 NPC（用于查目标对象）
            player: 玩家状态（目标可能是玩家）

        Returns:
            ActionVerdict
        """
        all_npcs = all_npcs or {}
        action_type = (action.get("action_type") or action.get("action") or "").strip()
        target_name = (action.get("target_name") or action.get("target") or "").strip()
        target_id = action.get("target_id", "")
        location = action.get("location", "") or actor.current_location
        day = world_state.current_day

        verdict = ActionVerdict(
            action_type=action_type,
            actor_id=actor.agent_id,
            actor_name=actor.name,
            target_id=target_id,
            target_name=target_name,
            location=location,
            day=day,
        )

        # Step 1: 可行性判定
        feasible, reason = self._check_feasibility(action, actor, world_state, all_npcs)
        verdict.feasible = feasible
        verdict.feasible_reason = reason
        if not feasible:
            verdict.narrative_summary = f"{actor.name}本欲{action_type}，但{reason}，未能成行。"
            verdict.should_broadcast = False
            return verdict

        # Step 2: 解析目标
        target = self._resolve_target(target_name, target_id, all_npcs, player)

        # Step 3: 分类裁决
        if action_type in (COMBAT_ACTIONS | STEALTH_ACTIONS | SOCIAL_ACTIONS):
            # 对抗性：走 dice
            if target is None:
                verdict.feasible = False
                verdict.feasible_reason = "找不到目标对象"
                verdict.narrative_summary = f"{actor.name}欲{action_type}，但目标不在场。"
                verdict.should_broadcast = False
                return verdict
            dice_result = self.dice.roll_action(
                actor=actor, target=target,
                action_type=action_type, world_state=world_state,
                context=self._build_context(action),
            )
            verdict.dice_result = dice_result
            verdict.executed = dice_result.success or dice_result.partial
            verdict.actor_effects = self._filter_actor_effects(dice_result.suggested_effects)
            verdict.target_effects = self._filter_target_effects(dice_result.suggested_effects)
            verdict.severity = dice_result.severity
            verdict.narrative_summary = self._build_combat_narrative(
                actor, target, action_type, dice_result,
            )
            # 应用状态变化
            self._apply_effects(actor, target, verdict.actor_effects, verdict.target_effects)
            # 对抗性事件必广播
            verdict.should_broadcast = True
            verdict.broadcast_radius = 3 if action_type in COMBAT_ACTIONS else 2

        elif action_type in MOVE_ACTIONS:
            # 移动：仅更新位置
            new_loc = action.get("destination") or action.get("target_location") or target_name
            if new_loc and new_loc != actor.current_location:
                actor.current_location = new_loc
            verdict.executed = True
            verdict.narrative_summary = f"{actor.name}前往{new_loc}。"
            verdict.should_broadcast = False  # 移动不广播（避免事件洪流）

        elif action_type in SOLO_ACTIONS:
            # 独处行为：消耗体力，可能小幅恢复
            verdict.executed = True
            energy_cost = self._solo_energy_cost(action_type)
            if actor.stats.energy >= energy_cost:
                actor.stats.energy = max(0, actor.stats.energy - energy_cost)
                # 修炼/冥想可能小幅提升
                if action_type in ("修炼", "练功", "冥想"):
                    actor.stats.energy = min(actor.stats.max_energy, actor.stats.energy + 5)
                verdict.actor_effects = {"energy_delta": -energy_cost}
            verdict.narrative_summary = f"{actor.name}{action_type}。"
            verdict.should_broadcast = False

        elif action_type in SOCIAL_FRIENDLY:
            # 友好社交：若目标在场则成功，否则失败
            if target is None:
                verdict.feasible = False
                verdict.feasible_reason = "目标不在场"
                verdict.narrative_summary = f"{actor.name}欲{action_type}，但目标不在场。"
                verdict.should_broadcast = False
                return verdict
            verdict.executed = True
            # 友好社交小幅提升好感
            favor_delta = 3 if action_type == "赠送" else 1
            verdict.target_effects = {"favor_delta": favor_delta}
            self._apply_effects(actor, target, {}, verdict.target_effects)
            verdict.narrative_summary = f"{actor.name}与{target.name}{action_type}。"
            verdict.should_broadcast = True
            verdict.broadcast_radius = 1

        elif action_type in SCHEME_ACTIONS:
            # [v1.2] 谋划行为：暗中布局/传讯
            # 走轻量智力对抗（actor.intelligence vs target.intelligence）
            # 失败可能反噬：被识破、好感下降、暴露企图
            scheme_result = self._adjudicate_scheme(
                action, action_type, actor, target, world_state, all_npcs, player, day,
            )
            verdict.executed = scheme_result["executed"]
            verdict.actor_effects = scheme_result["actor_effects"]
            verdict.target_effects = scheme_result["target_effects"]
            verdict.severity = scheme_result["severity"]
            verdict.narrative_summary = scheme_result["narrative_summary"]
            # 谋划行为广播半径小（隐秘）
            verdict.should_broadcast = scheme_result["should_broadcast"]
            verdict.broadcast_radius = scheme_result["broadcast_radius"]
            # 应用状态变化
            if target is not None:
                self._apply_effects(actor, target,
                                    verdict.actor_effects, verdict.target_effects)
            elif verdict.actor_effects:
                self._apply_effects(actor, None, verdict.actor_effects, {})

        else:
            # 未分类行动：默认通过，仅记日志
            verdict.executed = True
            verdict.narrative_summary = f"{actor.name}执行了「{action_type}」。"
            verdict.should_broadcast = False

        # Step 4: 写入行动记录
        actor.recent_actions.append({
            "day": day,
            "action": action_type,
            "detail": verdict.narrative_summary,
            "executed": verdict.executed,
            "severity": verdict.severity,
        })
        if len(actor.recent_actions) > 10:
            actor.recent_actions = actor.recent_actions[-10:]
        actor.last_action_day = day

        # Step 5: 广播事件
        if verdict.should_broadcast and self.event_bus:
            self._broadcast_action(verdict, all_npcs)

        return verdict

    # ===== 可行性判定 =====

    def _check_feasibility(self, action: dict, actor: "NPCState",
                            world_state: "WorldState",
                            all_npcs: dict) -> tuple[bool, str]:
        """检查行动是否违反世界规则"""
        action_type = (action.get("action_type") or action.get("action") or "").strip()

        # 死人不能行动
        if "已故" in (actor.tags or []):
            return False, "已故之人不能再行动"

        # 昏迷/垂死不能行动
        if any(s in (actor.status_effects or []) for s in ("昏迷", "垂死", "囚禁")):
            return False, "处于无法行动的状态"

        # 移动类：目标地点必须存在
        if action_type in MOVE_ACTIONS:
            new_loc = action.get("destination") or action.get("target_location")
            if new_loc:
                known = self._get_known_locations(world_state)
                if known and new_loc not in known:
                    # 宽松匹配（location code 可能不同）
                    if not any(new_loc in k or k in new_loc for k in known):
                        return False, f"地点「{new_loc}」不存在"

        # 修炼/练功需要最低体力
        if action_type in ("修炼", "练功", "冥想"):
            if actor.stats.energy < 10:
                return False, "体力不足，无法修炼"

        # 对抗类：必须能找到目标
        if action_type in (COMBAT_ACTIONS | STEALTH_ACTIONS | SOCIAL_ACTIONS):
            target_name = action.get("target_name") or action.get("target")
            if not target_name:
                return False, "未指定目标"

        # [v1.2] 谋划行为可行性检查
        if action_type in SCHEME_ACTIONS:
            # 暗中布局类必须有目标
            if action_type not in self.MESSENGER_ACTIONS:
                target_name = action.get("target_name") or action.get("target")
                if not target_name:
                    return False, "未指定谋划目标"
            # 谋划行为消耗体力，需要最低 5
            if actor.stats.energy < 5:
                return False, "体力不足，无法谋划"
            # 被识破状态下的连续谋划更易失败（不强制阻止，但记录提示）
            # 此处仅做硬性可行性，软性概率交给 dice

        return True, ""

    def _get_known_locations(self, world_state: "WorldState") -> list[str]:
        """从 world_state 获取已知地点列表"""
        known = getattr(world_state, "known_locations", None) or []
        locations = getattr(world_state, "locations", None)
        if locations and isinstance(locations, dict):
            known = list(known) + list(locations.keys())
        elif locations and isinstance(locations, list):
            known = list(known) + locations
        return known

    # ===== 目标解析 =====

    def _resolve_target(self, target_name: str, target_id: str,
                         all_npcs: dict, player: "PlayerState | None"):
        """根据 name/id 找到目标对象（NPC 或玩家）"""
        if not target_name and not target_id:
            return None

        # 优先按 id
        if target_id and target_id in all_npcs:
            return all_npcs[target_id]

        # 玩家
        if player and target_name:
            if target_name in (player.name, "主角", "玩家", "我"):
                return player

        # 按 name 模糊匹配
        if target_name:
            for nid, npc in all_npcs.items():
                if target_name in npc.name or npc.name in target_name:
                    return npc
                if nid == target_id:
                    return npc

        return None

    def _build_context(self, action: dict) -> dict:
        """从 action dict 提取 dice 修正上下文"""
        ctx = {}
        detail = (action.get("detail") or action.get("description") or "").lower()
        if any(k in detail for k in ("暗中", "偷袭", "伏击", "潜入")):
            ctx["ambush"] = True
        if "伏击" in detail:
            ctx["伏击"] = True
        return ctx

    # ===== [v1.2] 谋划行为裁决 =====

    # 传讯类行动（不需要直接目标在场，可托人传话）
    MESSENGER_ACTIONS = {
        "托人传递消息", "传话", "送信", "飞鸽传书", "飞剑传讯",
    }

    def _adjudicate_scheme(
        self,
        action: dict,
        action_type: str,
        actor: "NPCState",
        target,  # NPCState | PlayerState | None
        world_state: "WorldState",
        all_npcs: dict,
        player: "PlayerState | None",
        day: int,
    ) -> dict:
        """[v1.2] 谋划行为裁决。

        分两类处理：
          1. 暗中布局类（设局/算计/离间/收买/暗中打探）：
             - 需要目标在场（或目标为玩家）
             - 走轻量智力对抗（actor.intelligence vs target.intelligence）
             - 成功：对 target 施加隐藏影响（favor/tag/status 变化），不广播
             - 失败：被识破，actor 反受其害（favor 下降，可能被打上「被识破」tag）

          2. 传讯类（托人传递消息/送信/飞鸽传书等）：
             - 不要求目标在场（可托中间人传话）
             - 默认成功，但可能被识破（取决于行动者的隐秘能力）
             - 产生「消息已送达」事件，可触发目标后续反应
             - 广播半径极小（仅同场景且观察力强者可能察觉）
        """
        is_messenger = action_type in self.MESSENGER_ACTIONS

        # 传讯类：消息内容（从 action.detail 提取）
        message_content = (action.get("detail") or action.get("message") or "").strip()
        # 中间人（若指定）
        messenger_name = (action.get("messenger") or action.get("via") or "").strip()

        # ── 传讯类处理 ──
        if is_messenger:
            return self._adjudicate_messenger(
                action_type, actor, target, message_content, messenger_name, day,
            )

        # ── 暗中布局类处理 ──
        # 必须有目标
        if target is None:
            return {
                "executed": False,
                "actor_effects": {},
                "target_effects": {},
                "severity": "minor",
                "narrative_summary": f"{actor.name}欲{action_type}，但目标不在场，谋划落空。",
                "should_broadcast": False,
                "broadcast_radius": 0,
            }

        # 智力对抗判定（用 dice_engine 的 roll_action）
        # 把"谋划"映射为 dice 可识别的对抗：actor.intelligence vs target.intelligence
        try:
            dice_result = self.dice.roll_action(
                actor=actor, target=target,
                action_type="欺骗",  # 复用欺骗的 dice 逻辑（智力对抗）
                world_state=world_state,
                context=self._build_scheme_context(action, action_type),
            )
        except Exception as e:
            logger.warning("[Arbiter] scheme dice roll failed: %s", e)
            dice_result = None

        success = bool(dice_result and (dice_result.success or dice_result.partial))
        exposed = bool(dice_result and not dice_result.success and not dice_result.partial)

        actor_effects: dict = {}
        target_effects: dict = {}
        severity = "normal"
        narrative = ""
        should_broadcast = False
        broadcast_radius = 0

        if success:
            # 谋划成功：对目标施加隐藏影响
            # 收买 → 给目标加「被收买」tag，favor 上升
            # 离间 → 给目标与第三方关系破坏（简化为 favor 下降）
            # 设局/算计 → 目标状态变化（如「中计」status）
            # 暗中打探 → 获取目标信息（无直接效果，记录到 actor.recent_actions）
            if action_type == "收买":
                target_effects = {
                    "tags_add": ["被收买"],
                    "favor_delta": 10,
                }
                narrative = f"{actor.name}暗中收买了{target.name}。"
            elif action_type == "离间":
                target_effects = {
                    "status_add": "心生疑虑",
                    "favor_delta": -5,  # 对玩家方好感下降（被离间成功）
                }
                narrative = f"{actor.name}暗中离间，{target.name}心生疑虑。"
            elif action_type in ("设局", "算计", "布局"):
                target_effects = {
                    "status_add": "中计",
                }
                narrative = f"{actor.name}设局成功，{target.name}已入彀中。"
            elif action_type == "暗中打探":
                # 打探不直接改变目标，仅记录到 actor 的 recent_actions
                actor_effects = {"energy_delta": -5}
                narrative = f"{actor.name}暗中打探到了{target.name}的消息。"
            else:
                # 通用谋划
                target_effects = {"favor_delta": -3}
                narrative = f"{actor.name}对{target.name}的{action_type}得手。"

            severity = "major" if action_type in ("收买", "离间") else "normal"
            # 谋划成功不广播（隐秘行事）
            should_broadcast = False
            broadcast_radius = 0
        else:
            # 谋划失败
            if exposed:
                # 被识破：actor 反受其害
                actor_effects = {
                    "tags_add": ["被识破"],
                    "favor_delta": -10,  # 对目标好感下降（被识破后目标不悦）
                }
                severity = "major"
                narrative = f"{actor.name}欲{action_type}{target.name}，却被识破，反受其害。"
                # 被识破时同场景的 NPC 可能察觉
                should_broadcast = True
                broadcast_radius = 1
            else:
                # 普通失败：谋划落空，无副作用
                actor_effects = {"energy_delta": -5}
                severity = "minor"
                narrative = f"{actor.name}的{action_type}未能得手。"
                should_broadcast = False
                broadcast_radius = 0

        return {
            "executed": success,
            "actor_effects": actor_effects,
            "target_effects": target_effects,
            "severity": severity,
            "narrative_summary": narrative,
            "should_broadcast": should_broadcast,
            "broadcast_radius": broadcast_radius,
        }

    def _adjudicate_messenger(
        self,
        action_type: str,
        actor: "NPCState",
        target,  # 收信人，可为 None（托人转交但不知最终送达）
        message_content: str,
        messenger_name: str,
        day: int,
    ) -> dict:
        """[v1.2] 传讯类行动裁决。

        传讯类行动通常成功（除非被识破/拦截），产生以下效果：
          - 消息被送达（记录到 actor.recent_actions 和 target.recent_actions）
          - 广播半径 0（完全隐秘）
          - 体力小幅消耗
          - 若指定中间人，中间人可能泄露（暂不实现，留作未来扩展）
        """
        # 默认成功
        actor_effects = {"energy_delta": -3}
        target_effects: dict = {}

        # 构造消息摘要（避免泄漏完整内容到广播）
        msg_preview = message_content[:30] + "…" if len(message_content) > 30 else message_content
        if not msg_preview:
            msg_preview = "（无内容）"

        if target is not None:
            # 消息送达：target 收到消息（记录到其 recent_actions）
            target_effects = {
                "status_add": "收到消息",
            }
            # 同时把消息写入 target.recent_actions（不通过 _apply_to_one，直接追加）
            try:
                target.recent_actions.append({
                    "day": day,
                    "action": "收到消息",
                    "detail": f"收到{actor.name}的传讯：{msg_preview}",
                    "from": actor.name,
                })
                if len(target.recent_actions) > 10:
                    target.recent_actions = target.recent_actions[-10:]
            except Exception:
                pass

            narrative = f"{actor.name}通过{action_type}向{target.name}传话：{msg_preview}"
        else:
            # 无明确收信人：消息寄出但不知送达
            narrative = f"{actor.name}{action_type}，消息已寄出。"

        # 中间人提示
        if messenger_name:
            narrative += f"（托{messenger_name}转交）"

        return {
            "executed": True,
            "actor_effects": actor_effects,
            "target_effects": target_effects,
            "severity": "minor",
            "narrative_summary": narrative,
            # 传讯行为不广播（隐秘）
            "should_broadcast": False,
            "broadcast_radius": 0,
        }

    def _build_scheme_context(self, action: dict, action_type: str) -> dict:
        """[v1.2] 构造谋划行为的 dice 修正上下文"""
        ctx = {}
        detail = (action.get("detail") or action.get("description") or "").lower()

        # 暗中行动：actor 有隐蔽加成
        if any(k in detail for k in ("暗中", "私下", "秘密", "悄悄")):
            ctx["stealth"] = True

        # 收买类：金钱加成
        if action_type == "收买":
            ctx["bribe"] = True

        # 离间类：需要更高智力
        if action_type == "离间":
            ctx["intrigue"] = True

        # 利用对方弱点
        if any(k in detail for k in ("弱点", "把柄", "秘密")):
            ctx["leverage"] = True

        return ctx

    # ===== 状态变化应用 =====

    def _filter_actor_effects(self, suggested: dict) -> dict:
        """提取属于行动者的状态变化"""
        effects = {}
        for k, v in suggested.items():
            if k.startswith("actor_"):
                effects[k[len("actor_"):]] = v
        return effects

    def _filter_target_effects(self, suggested: dict) -> dict:
        """提取属于目标的状态变化"""
        effects = {}
        for k, v in suggested.items():
            if k.startswith("target_"):
                effects[k[len("target_"):]] = v
        return effects

    def _apply_effects(self, actor, target, actor_effects: dict, target_effects: dict):
        """安全地应用状态变化到 actor 和 target"""
        # Actor
        if actor_effects:
            self._apply_to_one(actor, actor_effects)
        # Target
        if target and target_effects:
            self._apply_to_one(target, target_effects)

    def _apply_to_one(self, who: "NPCState | PlayerState", effects: dict):
        """把单组状态变化应用到对象"""
        stats = getattr(who, "stats", None)
        if stats is None:
            return

        # 血量
        if "health_delta" in effects:
            delta = effects["health_delta"]
            new_hp = max(0, min(getattr(stats, "max_health", 100), stats.health + delta))
            stats.health = new_hp
            # 血量到 0 → 标记已故
            if new_hp == 0 and hasattr(who, "tags") and "已故" not in who.tags:
                who.tags.append("已故")

        # 体力
        if "energy_delta" in effects:
            delta = effects["energy_delta"]
            new_e = max(0, min(getattr(stats, "max_energy", 100), stats.energy + delta))
            stats.energy = new_e

        # 好感
        if "favor_delta" in effects:
            delta = effects["favor_delta"]
            rel = getattr(who, "relation_to_player", None)
            if rel is not None:
                rel.favor = max(-100, min(100, getattr(rel, "favor", 50) + delta))

        # 状态效果新增
        if "status_add" in effects:
            new_statuses = effects["status_add"]
            if isinstance(new_statuses, str):
                new_statuses = [new_statuses]
            existing = getattr(who, "status_effects", []) or []
            for s in new_statuses:
                if s not in existing:
                    existing.append(s)
            try:
                who.status_effects = existing
            except (AttributeError, TypeError):
                pass

        # tags 新增
        if "tags_add" in effects:
            new_tags = effects["tags_add"]
            if isinstance(new_tags, str):
                new_tags = [new_tags]
            existing = getattr(who, "tags", []) or []
            for t in new_tags:
                if t not in existing:
                    existing.append(t)
            try:
                who.tags = existing
            except (AttributeError, TypeError):
                pass

    def _solo_energy_cost(self, action_type: str) -> int:
        """独处行为的体力消耗"""
        return {
            "修炼": 15, "练功": 20, "冥想": 10,
            "采药": 8, "读书": 5, "整理": 5, "休息": -30,  # 休息是负消耗（恢复）
        }.get(action_type, 10)

    # ===== 叙事构建 =====

    def _build_combat_narrative(self, actor, target, action_type,
                                  dice_result: DiceResult) -> str:
        """构建对抗性行动的叙事摘要"""
        if dice_result.success and not dice_result.partial:
            outcome = "得手"
        elif dice_result.partial:
            outcome = "勉强得手，但付出代价" if dice_result.margin >= 0 else "虽未全功，仍有斩获"
        else:
            if dice_result.margin >= -5:
                outcome = "未能得手，反受小挫"
            else:
                outcome = "惨败而归"

        return (f"{actor.name}对{target.name}发起「{action_type}」，"
                f"{outcome}。{dice_result.reason}")

    # ===== 事件广播 =====

    def _broadcast_action(self, verdict: ActionVerdict, all_npcs: dict):
        """把行动裁决广播到 EventBus，供在场 NPC 即时反应"""
        if not self.event_bus:
            return
        try:
            event_data = {
                "event_type": "npc_action",
                "action_type": verdict.action_type,
                "actor_id": verdict.actor_id,
                "actor_name": verdict.actor_name,
                "target_id": verdict.target_id,
                "target_name": verdict.target_name,
                "location": verdict.location,
                "day": verdict.day,
                "severity": verdict.severity,
                "summary": verdict.narrative_summary,
                "broadcast_radius": verdict.broadcast_radius,
                "executed": verdict.executed,
            }
            self.event_bus.emit("on_npc_action", event_data)
        except Exception as e:
            logger.warning("Broadcast action failed: %s", e)


# 全局单例
_global_arbiter: ActionArbiter | None = None


def get_action_arbiter() -> ActionArbiter:
    """获取全局 ActionArbiter 单例"""
    global _global_arbiter
    if _global_arbiter is None:
        _global_arbiter = ActionArbiter()
    return _global_arbiter


def set_action_arbiter(arbiter: ActionArbiter):
    """注入带 EventBus 的 arbiter（由 game_engine 启动时调用）"""
    global _global_arbiter
    _global_arbiter = arbiter
