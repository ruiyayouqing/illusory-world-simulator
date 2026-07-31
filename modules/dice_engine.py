"""
[v1.2] 骰子引擎 — 为 NPC 行动裁决层提供确定性概率判定。

设计原则（与项目"代码管逻辑，LLM管叙事"一致）：
  - 不调 LLM，纯规则计算
  - 实力差驱动的对抗判定（刺杀/偷窃/挑战/辩论/逃脱）
  - 环境与状态修正（天气/地点/状态效果）
  - 结果三态：成功 / 部分成功 / 失败
  - 每次判定产出 DiceResult，含理由与状态变化建议

不引入新概念：复用 Stats 字段（health/energy/strength/agility/intelligence/luck 等）。
"""
from __future__ import annotations
import random
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schemas import NPCState, PlayerState, WorldState

logger = logging.getLogger("chronoverse.dice")


# ===== 对抗类型 → 使用的核心属性 =====
# 与 intent_classifier.py 的 stat 取值保持一致
ACTION_STAT_MAP = {
    "刺杀":       "agility",     # 暗杀走敏捷
    "偷窃":       "agility",
    "挑战":       "strength",    # 武力挑战走力量
    "比武":       "strength",
    "辩论":       "intelligence",
    "说服":       "intelligence",
    "欺骗":       "intelligence",
    "追踪":       "intelligence",
    "逃脱":       "agility",
    "潜行":       "agility",
    "强闯":       "strength",
    "胁迫":       "strength",
}


# ===== 状态效果修正 =====
# key: 状态效果字符串（NPCState.status_effects 中匹配），value: 修正值
POSITIVE_EFFECTS = {
    " berserk": 5, "狂暴": 5, "全神贯注": 4, "兴奋": 3, "祝福": 4,
    "激励": 3, "气血充盈": 3,
}
NEGATIVE_EFFECTS = {
    "重伤": -8, "昏迷": -15, "中毒": -6, "生病": -5, "病人": -5,
    "疲惫": -4, "饥饿": -3, "恐惧": -5, "醉酒": -3, "混乱": -4,
    "走火入魔": -10,
}


# ===== 地点修正（同地点优势） =====
# 攻击者在自家地盘 +2，被攻击者在自家地盘 +3（防御优势）
HOME_TURF_KEYWORDS = ("家", "府", "庄", "堂", "门", "派", "宗", "阁")


@dataclass
class DiceResult:
    """骰子判定结果"""
    success: bool              # 是否完全成功
    partial: bool = False      # 是否部分成功（边缘通过）
    margin: int = 0            # 成功/失败幅度（正数=成功幅度，负数=失败幅度）
    rolled: int = 0            # 骰子原始点数（1-20）
    difficulty: int = 10       # 难度等级
    modifier: int = 0          # 总修正值
    reason: str = ""           # 判定理由（供叙事引用）
    severity: str = "normal"   # 后果严重度：minor/normal/major/critical
    suggested_effects: dict = field(default_factory=dict)
    # suggested_effects 示例:
    # {"target_health_delta": -20, "actor_energy_delta": -10, "target_status_add": ["重伤"]}


class DiceEngine:
    """骰子引擎 — 单例设计，纯函数式判定"""

    def roll_action(
        self,
        actor: "NPCState | PlayerState",
        target: "NPCState | PlayerState",
        action_type: str,
        world_state: "WorldState" = None,
        context: dict = None,
    ) -> DiceResult:
        """对对抗性行动做骰子判定。

        Args:
            actor: 行动方（NPC 或玩家）
            target: 被行动方
            action_type: 行动类型（刺杀/偷窃/挑战/辩论/... 见 ACTION_STAT_MAP）
            world_state: 世界状态（用于环境修正）
            context: 额外上下文（如暗中/正面/伏击等修饰）

        Returns:
            DiceResult
        """
        context = context or {}
        stat_name = ACTION_STAT_MAP.get(action_type, "strength")

        # ===== 1. 计算双方实力 =====
        actor_stat = self._get_stat(actor, stat_name)
        target_stat = self._get_stat(target, stat_name)

        # ===== 2. 状态修正 =====
        actor_mod = self._status_modifier(actor)
        target_mod = self._status_modifier(target)

        # ===== 3. 环境修正 =====
        env_mod = self._environment_modifier(actor, target, world_state, context)

        # ===== 4. 伏击/偷袭加成 =====
        ambush_mod = 0
        if context.get("ambush") or context.get("偷袭") or context.get("暗中"):
            ambush_mod = 6  # 偷袭+6
        if context.get("伏击"):
            ambush_mod = 8

        # ===== 5. 装备/标签修正（用 tags 粗略推断） =====
        actor_eq_mod = self._equipment_modifier(actor)
        target_eq_mod = self._equipment_modifier(target)

        # ===== 6. 总修正 =====
        total_mod = (
            actor_mod + env_mod + ambush_mod + actor_eq_mod
            - target_mod - target_eq_mod
        )

        # ===== 7. 难度 = 目标属性 + 10（基础难度） =====
        difficulty = 10 + target_stat - actor_stat + 5  # 5 是基础防御值
        difficulty = max(5, min(25, difficulty))  # 难度钳制在 5-25

        # ===== 8. 掷骰（1d20 + 修正） =====
        rolled = random.randint(1, 20)
        total = rolled + total_mod

        margin = total - difficulty

        # ===== 9. 判定三态 =====
        if margin >= 5:
            success, partial = True, False
        elif margin >= 0:
            success, partial = True, True  # 边缘通过=部分成功
        elif margin >= -5:
            success, partial = False, True  # 边缘失败=部分失败（仍有效果）
        else:
            success, partial = False, False

        # ===== 10. 后果严重度 =====
        severity, effects = self._calc_severity(
            action_type, success, partial, margin, actor, target
        )

        # ===== 11. 构建理由 =====
        reason = self._build_reason(
            action_type, rolled, total_mod, difficulty, margin,
            success, partial, actor, target, severity,
        )

        return DiceResult(
            success=success,
            partial=partial,
            margin=margin,
            rolled=rolled,
            difficulty=difficulty,
            modifier=total_mod,
            reason=reason,
            severity=severity,
            suggested_effects=effects,
        )

    # ===== 工具方法 =====

    def _get_stat(self, who: "NPCState | PlayerState", stat_name: str) -> int:
        """安全读取属性值"""
        stats = getattr(who, "stats", None)
        if stats is None:
            return 10
        return int(getattr(stats, stat_name, 10) or 10)

    def _status_modifier(self, who: "NPCState | PlayerState") -> int:
        """根据 status_effects 计算修正"""
        mod = 0
        effects = getattr(who, "status_effects", []) or []
        for eff in effects:
            eff_str = str(eff)
            # 正面
            for k, v in POSITIVE_EFFECTS.items():
                if k in eff_str:
                    mod += v
            # 负面
            for k, v in NEGATIVE_EFFECTS.items():
                if k in eff_str:
                    mod += v
        # 体力低也是负面
        stats = getattr(who, "stats", None)
        if stats:
            energy = getattr(stats, "energy", 100)
            if energy < 30:
                mod -= 3
            health_ratio = getattr(stats, "health", 100) / max(getattr(stats, "max_health", 100), 1)
            if health_ratio < 0.3:
                mod -= 4
        return mod

    def _environment_modifier(self, actor, target, world_state, context) -> int:
        """环境修正：天气/地点"""
        mod = 0
        # 同地点：攻击者吃亏（防御方有地利）
        actor_loc = getattr(actor, "current_location", "") or getattr(actor, "location", "")
        target_loc = getattr(target, "current_location", "") or getattr(target, "location", "")
        if actor_loc and target_loc and actor_loc == target_loc:
            # 目标在自家地盘
            target_name = getattr(target, "name", "")
            for kw in HOME_TURF_KEYWORDS:
                if kw in target_loc or kw in (getattr(target, "role", "") or ""):
                    mod -= 3  # 攻击方吃亏
                    break

        # 天气修正（恶劣天气对敏捷类行动不利）
        if world_state:
            weather = (getattr(world_state, "weather", "") or "").lower()
            if any(w in weather for w in ("雨", "雪", "雾", "暴")):
                if context.get("stat") == "agility":
                    mod -= 2
        return mod

    def _equipment_modifier(self, who: "NPCState | PlayerState") -> int:
        """从 tags 推断装备/能力修正"""
        tags = getattr(who, "tags", []) or []
        mod = 0
        # 武力相关
        if any(t in tags for t in ("武者", "高手", "宗师", "剑客", "刀客")):
            mod += 3
        if any(t in tags for t in ("修士", "高僧", "真人", "金丹", "元婴")):
            mod += 5
        # 弱势
        if any(t in tags for t in ("幼年", "少年", "老迈", "退休", "病人")):
            mod -= 2
        if "已故" in tags:
            mod -= 20  # 死人不能行动
        return mod

    def _calc_severity(self, action_type: str, success: bool, partial: bool,
                        margin: int, actor, target) -> tuple[str, dict]:
        """根据行动类型与判定结果，计算后果严重度与建议状态变化"""
        severity = "normal"
        effects: dict = {}

        # 行动类型决定基础伤害
        base_damage = {
            "刺杀": 50, "挑战": 30, "比武": 25, "强闯": 25, "胁迫": 15,
            "偷窃": 0, "辩论": 0, "说服": 0, "欺骗": 0, "追踪": 0,
            "逃脱": 0, "潜行": 0,
        }.get(action_type, 15)

        if success and not partial:
            # 完全成功
            if action_type in ("刺杀", "挑战", "比武", "强闯"):
                severity = "critical"
                effects["target_health_delta"] = -int(base_damage * 1.5)
                effects["actor_energy_delta"] = -10
                if action_type == "刺杀" and margin >= 10:
                    effects["target_status_add"] = ["昏迷"]
                    severity = "critical"
            elif action_type in ("偷窃",):
                severity = "normal"
                effects["actor_energy_delta"] = -5
            elif action_type in ("说服", "欺骗", "辩论"):
                severity = "normal"
                effects["target_favor_delta"] = 5
            elif action_type in ("逃脱", "潜行"):
                severity = "normal"
                effects["actor_energy_delta"] = -5
        elif partial:
            # 部分成功
            if action_type in ("刺杀", "挑战", "比武"):
                severity = "major"
                effects["target_health_delta"] = -base_damage
                effects["actor_energy_delta"] = -15
                if action_type == "刺杀":
                    effects["target_status_add"] = ["重伤"]
            elif action_type in ("偷窃",):
                severity = "minor"
                effects["actor_energy_delta"] = -8
                effects["caught"] = True  # 被发现
            elif action_type in ("逃脱", "潜行"):
                severity = "minor"
                effects["actor_energy_delta"] = -8
                effects["partial_escape"] = True
            elif action_type in ("说服", "辩论"):
                severity = "minor"
                effects["target_favor_delta"] = 2
        else:
            # 失败
            if margin >= -5:
                # 边缘失败
                if action_type in ("刺杀", "挑战"):
                    severity = "minor"
                    effects["actor_health_delta"] = -10
                    effects["actor_energy_delta"] = -15
                    effects["actor_status_add"] = ["暴露"]
                elif action_type in ("偷窃",):
                    severity = "major"
                    effects["caught"] = True
                    effects["target_favor_delta"] = -15
                elif action_type in ("逃脱", "潜行"):
                    severity = "minor"
                    effects["actor_energy_delta"] = -10
                    effects["failed_escape"] = True
                else:
                    severity = "minor"
                    effects["actor_energy_delta"] = -8
            else:
                # 惨败
                severity = "major"
                if action_type in ("刺杀", "挑战", "比武"):
                    effects["actor_health_delta"] = -25
                    effects["actor_energy_delta"] = -20
                    effects["actor_status_add"] = ["重伤"]
                elif action_type in ("偷窃",):
                    effects["caught"] = True
                    effects["target_favor_delta"] = -25
                    effects["actor_status_add"] = ["声名狼藉"]
                else:
                    effects["actor_energy_delta"] = -10

        return severity, effects

    def _build_reason(self, action_type, rolled, modifier, difficulty, margin,
                       success, partial, actor, target, severity) -> str:
        """构建判定理由文本，供叙事引用"""
        actor_name = getattr(actor, "name", "行动者")
        target_name = getattr(target, "name", "目标")
        roll_str = f"骰{rolled}+修正{modifier:+d}={rolled+modifier} vs 难度{difficulty}"

        if success and not partial:
            outcome = "成功"
        elif partial:
            outcome = "勉强成功（付出代价）" if margin >= 0 else "勉强失败（仍有效果）"
        else:
            outcome = "失败"

        return (f"{actor_name}对{target_name}发起「{action_type}」——"
                f"{roll_str}（幅度{margin:+d}），{outcome}，严重度：{severity}")


# 全局单例
_global_engine: DiceEngine | None = None


def get_dice_engine() -> DiceEngine:
    """获取全局 DiceEngine 单例"""
    global _global_engine
    if _global_engine is None:
        _global_engine = DiceEngine()
    return _global_engine
