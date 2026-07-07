from __future__ import annotations
import random
from .schemas import PlayerState, WorldState


# 自动经验来源和数值
EXP_SOURCES = {
    "action": {"base": 8, "variance": 7},      # 普通行动 +8±7
    "event": {"base": 25, "variance": 15},     # 世界事件 +25±15
    "fight": {"base": 35, "variance": 25},     # 战斗 +35±25
    "crisis": {"base": 50, "variance": 30},    # 危机/生死 +50±30
    "treasure": {"base": 40, "variance": 20},  # 得到宝物 +40±20
    "pill": {"base": 30, "variance": 20},      # 服用丹药 +30±20
    "rest": {"base": 3, "variance": 2},        # 休息/日常 +3±2
    "dialogue": {"base": 5, "variance": 3},    # 重要对话 +5±3
}

LEVEL_SYSTEMS = {
    "martial": {
        "name": "武道境界",
        "levels": [
            {"name": "凡人", "min_exp": 0, "stat_bonus": {}},
            {"name": "炼体初期", "min_exp": 50, "stat_bonus": {"strength": 1}},
            {"name": "炼体中期", "min_exp": 100, "stat_bonus": {"strength": 1}},
            {"name": "炼体后期", "min_exp": 180, "stat_bonus": {"strength": 1}},
            {"name": "炼体圆满", "min_exp": 300, "stat_bonus": {"strength": 2}},
            {"name": "内息初期", "min_exp": 450, "stat_bonus": {"strength": 2, "agility": 1}},
            {"name": "内息中期", "min_exp": 600, "stat_bonus": {"strength": 2, "agility": 1}},
            {"name": "内息后期", "min_exp": 800, "stat_bonus": {"strength": 2, "agility": 1}},
            {"name": "内息圆满", "min_exp": 1000, "stat_bonus": {"strength": 3, "agility": 1}},
            {"name": "先天初期", "min_exp": 1300, "stat_bonus": {"strength": 3, "agility": 2, "health": 10}},
            {"name": "先天中期", "min_exp": 1600, "stat_bonus": {"strength": 3, "agility": 2, "health": 10}},
            {"name": "先天后期", "min_exp": 2000, "stat_bonus": {"strength": 3, "agility": 2, "health": 10}},
            {"name": "先天圆满", "min_exp": 2500, "stat_bonus": {"strength": 5, "agility": 2, "health": 20}},
            {"name": "宗师", "min_exp": 3500, "stat_bonus": {"strength": 8, "agility": 4, "health": 50}},
            {"name": "大宗师", "min_exp": 6000, "stat_bonus": {"strength": 12, "agility": 6, "health": 80}},
            {"name": "武圣", "min_exp": 10000, "stat_bonus": {"strength": 20, "agility": 10, "health": 120}},
        ],
    },
    "cultivation": {
        "name": "修真境界",
        "levels": [
            {"name": "凡人", "min_exp": 0, "stat_bonus": {}},
            {"name": "炼气初期", "min_exp": 50, "stat_bonus": {"magic": 2}},
            {"name": "炼气中期", "min_exp": 100, "stat_bonus": {"magic": 2}},
            {"name": "炼气后期", "min_exp": 180, "stat_bonus": {"magic": 2}},
            {"name": "炼气圆满", "min_exp": 300, "stat_bonus": {"magic": 4}},
            {"name": "筑基初期", "min_exp": 500, "stat_bonus": {"magic": 5, "health": 10}},
            {"name": "筑基中期", "min_exp": 700, "stat_bonus": {"magic": 5, "health": 10}},
            {"name": "筑基后期", "min_exp": 950, "stat_bonus": {"magic": 5, "health": 10}},
            {"name": "筑基圆满", "min_exp": 1200, "stat_bonus": {"magic": 10, "health": 20}},
            {"name": "金丹初期", "min_exp": 1600, "stat_bonus": {"magic": 10, "health": 20, "strength": 1}},
            {"name": "金丹中期", "min_exp": 2100, "stat_bonus": {"magic": 10, "health": 20, "strength": 1}},
            {"name": "金丹后期", "min_exp": 2700, "stat_bonus": {"magic": 10, "health": 20, "strength": 1}},
            {"name": "金丹圆满", "min_exp": 3500, "stat_bonus": {"magic": 20, "health": 50, "strength": 3}},
            {"name": "元婴初期", "min_exp": 5000, "stat_bonus": {"magic": 20, "health": 30, "strength": 2, "agility": 1}},
            {"name": "元婴中期", "min_exp": 7000, "stat_bonus": {"magic": 20, "health": 30, "strength": 2, "agility": 1}},
            {"name": "元婴后期", "min_exp": 9500, "stat_bonus": {"magic": 20, "health": 30, "strength": 2, "agility": 1}},
            {"name": "元婴圆满", "min_exp": 12000, "stat_bonus": {"magic": 40, "health": 80, "strength": 5, "agility": 3}},
            {"name": "化神", "min_exp": 18000, "stat_bonus": {"magic": 70, "health": 120, "strength": 8, "agility": 5}},
            {"name": "渡劫", "min_exp": 30000, "stat_bonus": {"magic": 100, "health": 200, "strength": 12, "agility": 8}},
            {"name": "大乘", "min_exp": 50000, "stat_bonus": {"magic": 150, "health": 300, "strength": 15, "agility": 10}},
        ],
    },
    "magic": {
        "name": "魔法阶级",
        "levels": [
            {"name": "学徒", "min_exp": 0, "stat_bonus": {}},
            {"name": "见习法师", "min_exp": 80, "stat_bonus": {"magic": 3}},
            {"name": "正式法师", "min_exp": 200, "stat_bonus": {"magic": 5}},
            {"name": "元素师", "min_exp": 400, "stat_bonus": {"magic": 12}},
            {"name": "大法师", "min_exp": 800, "stat_bonus": {"magic": 25, "intelligence": 3}},
            {"name": "魔导士", "min_exp": 1800, "stat_bonus": {"magic": 45, "intelligence": 5}},
            {"name": "大魔导士", "min_exp": 4000, "stat_bonus": {"magic": 75, "intelligence": 8}},
            {"name": "法圣", "min_exp": 8000, "stat_bonus": {"magic": 120, "intelligence": 12}},
        ],
    },
    "none": {
        "name": "无等级",
        "levels": [{"name": "普通人", "min_exp": 0, "stat_bonus": {}}],
    },
}


class LevelSystem:
    def __init__(self, system_type: str = "none"):
        self.system_type = system_type
        self.config = LEVEL_SYSTEMS.get(system_type, LEVEL_SYSTEMS["none"])
        self.experience: int = 0

    def get_all_level_names(self) -> list[str]:
        return [lv["name"] for lv in self.config.get("levels", [])]

    def get_system_types(self) -> list[dict]:
        return [{"id": k, "name": v["name"], "levels": len(v["levels"])}
                for k, v in LEVEL_SYSTEMS.items()]

    def get_current_level(self) -> dict:
        levels = self.config["levels"]
        current = levels[0]
        for level in levels:
            if self.experience >= level["min_exp"]:
                current = level
        return current

    def get_next_level(self) -> dict | None:
        levels = self.config["levels"]
        current = self.get_current_level()
        for i, level in enumerate(levels):
            if level["name"] == current["name"] and i + 1 < len(levels):
                return levels[i + 1]
        return None

    def calc_level_progress(self) -> float:
        """计算当前等级进度(0.0~1.0)，用于判断是否接近升级"""
        current = self.get_current_level()
        next_level = self.get_next_level()
        if not next_level:
            return 1.0
        span = next_level["min_exp"] - current["min_exp"]
        if span <= 0:
            return 1.0
        return (self.experience - current["min_exp"]) / span

    def is_near_level_up(self, threshold: float = 0.75) -> bool:
        """是否接近升级（默认75%进度触发蝴蝶效应提示）"""
        return self.calc_level_progress() >= threshold

    def get_near_level_up_hint(self) -> str | None:
        """获取接近升级时的暗示文本"""
        if not self.is_near_level_up():
            return None
        current = self.get_current_level()
        next_level = self.get_next_level()
        if not next_level:
            return None
        hints = [
            f"你感到体内的力量在涌动，距离{next_level['name']}似乎只有一步之遥...",
            f"丹田中的灵力愈发浑厚，突破至{next_level['name']}的契机近在眼前。",
            f"冥冥中你有所感应，{next_level['name']}的门槛已在眼前。",
            f"近日修炼时偶有顿悟之感，{next_level['name']}似乎不再是遥不可及。",
        ]
        return random.choice(hints)

    def calc_exp_for_action(self, action_type: str) -> int:
        """根据行动类型计算获得的经验值"""
        source = EXP_SOURCES.get(action_type, EXP_SOURCES["action"])
        base = source["base"]
        variance = source["variance"]
        return max(1, base + random.randint(-variance, variance))

    def add_experience(self, amount: int) -> dict:
        old_level = self.get_current_level()
        self.experience += amount
        new_level = self.get_current_level()

        result = {
            "exp_gained": amount,
            "old_level": old_level["name"],
            "new_level": new_level["name"],
            "leveled_up": old_level["name"] != new_level["name"],
        }

        if result["leveled_up"]:
            result["bonus"] = new_level["stat_bonus"]
            next_level = self.get_next_level()
            if next_level:
                result["next_level"] = next_level["name"]
                result["exp_to_next"] = next_level["min_exp"] - self.experience

        return result

    def apply_level_bonuses(self, player: PlayerState) -> dict:
        level = self.get_current_level()
        bonus = level.get("stat_bonus", {})
        changes = {}
        for stat, value in bonus.items():
            old = getattr(player.stats, stat, 0)
            setattr(player.stats, stat, old + value)
            changes[stat] = {"old": old, "new": old + value}
        return {"level": level["name"], "changes": changes}

    def get_level_narrative(self) -> str:
        level = self.get_current_level()
        next_level = self.get_next_level()
        if next_level:
            progress = ((self.experience - level["min_exp"]) /
                        (next_level["min_exp"] - level["min_exp"]) * 100)
            return f"【{self.config['name']}】{level['name']} ({self.experience}/{next_level['min_exp']}, {progress:.0f}%)"
        return f"【{self.config['name']}】{level['name']} (已满级)"

    def to_dict(self) -> dict:
        # 序列化等级系统状态（config 由 system_type 派生，无需持久化）
        return {
            "system_type": self.system_type,
            "experience": self.experience,
        }

    def from_dict(self, data: dict):
        self.system_type = data.get("system_type", "none")
        self.config = LEVEL_SYSTEMS.get(self.system_type, LEVEL_SYSTEMS["none"])
        self.experience = data.get("experience", 0)


class GodsCodex:
    def __init__(self):
        self.rules: list[dict] = []
        self.violations: list[dict] = []

    def add_rule(self, rule_type: str, description: str, severity: str = "warning"):
        self.rules.append({"type": rule_type, "description": description, "severity": severity})

    def check_violation(self, action: str, player: PlayerState, world_state: WorldState) -> dict | None:
        for rule in self.rules:
            if self._matches_rule(rule, action, player, world_state):
                return {"rule": rule["description"], "type": rule["type"], "severity": rule["severity"], "action": action}
        return None

    def _matches_rule(self, rule: dict, action: str, player: PlayerState, world_state: WorldState) -> bool:
        rt = rule.get("type", "")
        if rt == "no_time_travel": return "回到" in action or "穿越回" in action
        if rt == "no_god_mode": return "无敌" in action or "不死" in action
        if rt == "respect_history": return "改变历史" in action and world_state.world_type == "historical"
        return False

    def initialize_default_rules(self, world_type: str = "historical"):
        self.rules = []
        self.add_rule("no_time_travel", "你无法穿越时间，只能向前", "hard")
        self.add_rule("no_god_mode", "你不是神，会受伤会死亡", "hard")
        if world_type == "historical":
            self.add_rule("respect_history", "历史大势不可逆转，但细节可以改变", "soft")


class DestinyRegret:
    def __init__(self):
        self.missed_opportunities: list[dict] = []

    def record_missed(self, opportunity_type: str, description: str, day: int):
        self.missed_opportunities.append({"type": opportunity_type, "description": description, "day": day})

    def check_regret(self, player: PlayerState, world_state: WorldState) -> dict | None:
        if player.age >= 40 and not self.missed_opportunities:
            return {"type": "midlife_crisis", "message": "你已经40岁了，回顾这一生，似乎错过了很多机会..."}
        if player.age >= 50 and player.social.gold < 500:
            return {"type": "poverty_regret", "message": "50岁了还身无分文，当年要是好好攒钱就好了..."}
        return None
