"""
[v9] 世界规则引擎 — 确定性的世界因果计算

设计原则：
  - 代码管逻辑，LLM管叙事
  - 规则引擎在LLM调用之前运行，输出确定性的状态变化
  - LLM只需要把状态变化写成故事，不需要做数值计算
"""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("chronoverse.world_rules")


@dataclass
class RuleEffect:
    """一条规则的效果"""
    stat_changes: dict = field(default_factory=dict)
    tag_add: list[str] = field(default_factory=list)
    tag_remove: list[str] = field(default_factory=list)
    effect_add: list[str] = field(default_factory=list)
    effect_remove: list[str] = field(default_factory=list)
    relation_changes: dict = field(default_factory=dict)
    reputation_delta: int = 0
    crisis_delta: int = 0
    narrative_hint: str = ""


@dataclass
class RuleResult:
    """规则引擎的计算结果"""
    effects: list[RuleEffect] = field(default_factory=list)
    triggered_rules: list[str] = field(default_factory=list)
    narrative_hints: list[str] = field(default_factory=list)

    def merge(self, other: "RuleResult"):
        self.effects.extend(other.effects)
        self.triggered_rules.extend(other.triggered_rules)
        self.narrative_hints.extend(other.narrative_hints)

    def apply_to_player(self, player_state):
        """将所有效果应用到玩家状态"""
        for eff in self.effects:
            for stat, delta in eff.stat_changes.items():
                if hasattr(player_state.stats, stat):
                    old = getattr(player_state.stats, stat)
                    max_val = getattr(player_state.stats, f"max_{stat}", None)
                    new = old + delta
                    if max_val is not None:
                        new = min(new, max_val)
                    new = max(0, new)
                    setattr(player_state.stats, stat, new)
                elif stat == "gold":
                    player_state.social.gold = max(0, player_state.social.gold + delta)
                elif stat == "reputation":
                    player_state.social.reputation += delta

            if eff.tag_add:
                for t in eff.tag_add:
                    if t not in player_state.tags:
                        player_state.tags.append(t)
            if eff.tag_remove:
                player_state.tags = [t for t in player_state.tags if t not in eff.tag_remove]
            if eff.effect_add:
                for e in eff.effect_add:
                    if e not in player_state.status_effects and len(player_state.status_effects) < 15:
                        player_state.status_effects.append(e)
            if eff.effect_remove:
                player_state.status_effects = [e for e in player_state.status_effects if e not in eff.effect_remove]

            if eff.reputation_delta:
                player_state.social.reputation = max(
                    -100, min(100, player_state.social.reputation + eff.reputation_delta)
                )

    def apply_to_world(self, world_state):
        """将所有效果应用到世界状态"""
        for eff in self.effects:
            world_state.crisis_level = max(0, min(10, world_state.crisis_level + eff.crisis_delta))

    def get_narrative_context(self) -> str:
        """生成给LLM的叙事提示，让LLM知道发生了什么"""
        if not self.narrative_hints:
            return ""
        return "【世界规则引擎判定】\n" + "\n".join(f"- {h}" for h in self.narrative_hints)


class WorldRulesEngine:
    """世界规则引擎"""

    def __init__(self):
        self._action_rules: list[tuple] = []
        self._keyword_rules: list[tuple] = []
        self._location_rules: dict[str, list[tuple]] = {}
        self._time_rules: dict[str, list[tuple]] = {}
        self._init_default_rules()

    def _init_default_rules(self):
        """初始化默认规则集"""

        # === 暴力行为规则 ===
        # [Bug#16] 使用更精确的关键词，避免 "杀价"(砍价)、"杀鸡"(做饭) 等误匹配
        self._keyword_rule(
            keywords=["杀人", "杀死", "杀了", "斩杀", "刺死", "击毙", "了结", "取他性命",
                       "砍死", "一刀杀了", "要他命", "害命", "行凶"],
            name="暴力杀人",
            effect=RuleEffect(
                reputation_delta=-30,
                crisis_delta=2,
                narrative_hint="玩家实施了暴力杀人行为，城中人心惶惶",
                tag_add=["杀人犯"],
            ),
        )
        # [Bug#16] "打" 过于宽泛，匹配 "打扮"、"打听"、"打算" 等。改用更具体的词组
        self._keyword_rule(
            keywords=["打架", "殴打", "揍他", "暴打", "踢他", "痛殴", "猛击",
                       "打了他", "打了她", "出手打", "动手打"],
            name="肢体冲突",
            effect=RuleEffect(
                reputation_delta=-5,
                stat_changes={"energy": -3},
                narrative_hint="玩家与人发生了肢体冲突",
            ),
        )
        self._keyword_rule(
            keywords=["偷", "窃", "扒", "摸走", "顺手牵羊"],
            name="偷窃行为",
            effect=RuleEffect(
                reputation_delta=-10,
                narrative_hint="玩家实施了偷窃行为",
                tag_add=["小偷"],
            ),
        )

        # === 社交行为规则 ===
        self._keyword_rule(
            keywords=["请客", "宴请", "摆酒", "设宴"],
            name="宴请他人",
            effect=RuleEffect(
                stat_changes={"gold": -20, "reputation": 5},
                narrative_hint="玩家宴请他人，赢得了一些好感",
            ),
        )
        self._keyword_rule(
            keywords=["贿赂", "塞银子", "打点", "买通"],
            name="行贿",
            effect=RuleEffect(
                stat_changes={"gold": -50},
                reputation_delta=3,
                narrative_hint="玩家暗中打点，事情有了转机",
            ),
        )
        self._keyword_rule(
            keywords=["施舍", "捐款", "赈济", "救济", "捐赠"],
            name="慈善行为",
            effect=RuleEffect(
                stat_changes={"gold": -30, "reputation": 10},
                narrative_hint="玩家慷慨解囊，百姓感恩",
            ),
        )

        # === 探索行为规则 ===
        self._keyword_rule(
            keywords=["搜索", "搜查", "翻找", "搜遍", "仔细找"],
            name="仔细搜索",
            effect=RuleEffect(
                stat_changes={"energy": -5},
                narrative_hint="玩家仔细搜索周围环境",
            ),
        )
        self._keyword_rule(
            keywords=["观察", "环顾", "打量", "审视", "查看"],
            name="观察环境",
            effect=RuleEffect(
                stat_changes={"energy": -1},
                narrative_hint="玩家观察周围环境",
            ),
        )

        # === 休息行为规则 ===
        self._keyword_rule(
            keywords=["休息", "歇息", "睡觉", "入睡", "就寝", "打盹"],
            name="休息",
            effect=RuleEffect(
                stat_changes={"energy": 20, "health": 5},
                effect_remove=["疲惫"],
                narrative_hint="玩家得到充分休息，体力恢复",
            ),
        )
        self._keyword_rule(
            keywords=["疗伤", "治疗", "包扎", "敷药", "服药"],
            name="疗伤",
            effect=RuleEffect(
                stat_changes={"health": 15},
                effect_remove=["轻伤", "中毒"],
                narrative_hint="玩家进行了疗伤处理",
            ),
        )

        # === 修炼行为规则 ===
        self._keyword_rule(
            keywords=["修炼", "练功", "打坐", "运功", "吐纳", "闭关"],
            name="修炼",
            effect=RuleEffect(
                stat_changes={"energy": -10, "magic": 1},
                narrative_hint="玩家进行修炼，内力有所精进",
            ),
        )
        self._keyword_rule(
            keywords=["练剑", "练刀", "练武", "操练", "对练"],
            name="练武",
            effect=RuleEffect(
                stat_changes={"energy": -8, "strength": 1, "agility": 1},
                narrative_hint="玩家刻苦练武，身手有所进步",
            ),
        )

        # === 交易行为规则 ===
        self._keyword_rule(
            keywords=["购买", "买下", "购置", "采办"],
            name="购物",
            effect=RuleEffect(
                stat_changes={"gold": -15},
                narrative_hint="玩家花费银两购置物品",
            ),
        )
        self._keyword_rule(
            keywords=["出售", "卖掉", "变卖", "典当"],
            name="出售物品",
            effect=RuleEffect(
                stat_changes={"gold": 10},
                narrative_hint="玩家出售物品获得银两",
            ),
        )

    def _keyword_rule(self, keywords: list[str], name: str, effect: RuleEffect):
        """注册关键词规则"""
        self._keyword_rules.append((keywords, name, effect))

    def evaluate_player_action(self, player_input: str, player_state=None,
                               world_state=None, npc_states: dict = None) -> RuleResult:
        """评估玩家输入，返回规则引擎的计算结果"""
        result = RuleResult()

        # 关键词匹配规则
        for keywords, name, effect in self._keyword_rules:
            for kw in keywords:
                if kw in player_input:
                    result.effects.append(effect)
                    result.triggered_rules.append(name)
                    if effect.narrative_hint:
                        result.narrative_hints.append(effect.narrative_hint)
                    break

        # 基于玩家状态的被动规则
        if player_state:
            result.merge(self._evaluate_passive_rules(player_state, world_state))

        # 基于地点的规则
        if player_state and world_state:
            result.merge(self._evaluate_location_rules(
                player_state.location, world_state, npc_states))

        # 基于时间的规则
        if world_state:
            result.merge(self._evaluate_time_rules(world_state))

        return result

    def _evaluate_passive_rules(self, player_state, world_state=None) -> RuleResult:
        """基于玩家当前状态的被动规则"""
        result = RuleResult()

        # 低生命警告
        if player_state.stats.health <= player_state.stats.max_health * 0.2:
            if "重伤" not in player_state.status_effects:
                result.effects.append(RuleEffect(
                    effect_add=["重伤"],
                    narrative_hint="玩家生命垂危，伤势严重",
                ))
                result.triggered_rules.append("低生命警告")

        # 低体力警告
        if player_state.stats.energy <= 10:
            if "疲惫" not in player_state.status_effects:
                result.effects.append(RuleEffect(
                    effect_add=["疲惫"],
                    narrative_hint="玩家体力耗尽，精疲力竭",
                ))
                result.triggered_rules.append("低体力警告")

        # 状态效果交互
        effects = set(player_state.status_effects)
        if "中毒" in effects and "解毒" in effects:
            result.effects.append(RuleEffect(
                effect_remove=["中毒", "解毒"],
                narrative_hint="毒性被解药中和",
            ))
            result.triggered_rules.append("解毒交互")

        return result

    def _evaluate_location_rules(self, location: str, world_state,
                                  npc_states: dict = None) -> RuleResult:
        """基于地点的规则"""
        result = RuleResult()

        if not npc_states:
            return result

        nearby_npcs = [
            npc for npc in npc_states.values()
            if npc.current_location == location
        ]

        # 战斗场景人数修正
        if len(nearby_npcs) >= 3:
            result.narrative_hints.append(
                f"此处有{len(nearby_npcs)}人在场，局势复杂"
            )

        return result

    def _evaluate_time_rules(self, world_state) -> RuleResult:
        """基于时间的规则"""
        result = RuleResult()

        time = world_state.current_time
        if time == "深夜":
            result.narrative_hints.append("夜深人静，视野受限，行动需谨慎")
        elif time == "清晨":
            result.narrative_hints.append("晨光初露，万物苏醒")
        elif time == "傍晚":
            result.narrative_hints.append("夕阳西下，暮色渐浓")

        # 季节效果
        season = world_state.season
        if season == "冬季":
            result.narrative_hints.append("天寒地冻，需要注意保暖")
        elif season == "夏季":
            result.narrative_hints.append("酷暑难耐，体力消耗加快")

        return result

    def get_world_context_summary(self, player_state, world_state,
                                   npc_states: dict = None) -> str:
        """生成世界规则上下文摘要，注入到LLM prompt中"""
        parts = []

        # 危机等级描述
        if world_state and world_state.crisis_level >= 7:
            parts.append("【危机】天下大乱，民不聊生")
        elif world_state and world_state.crisis_level >= 4:
            parts.append("【动荡】时局不稳，暗流涌动")
        elif world_state and world_state.crisis_level >= 1:
            parts.append("【隐患】表面平静，实则暗藏危机")

        # 玩家状态摘要
        if player_state:
            if player_state.stats.health <= 30:
                parts.append("【状态】玩家身受重伤，行动受限")
            elif player_state.stats.health <= 60:
                parts.append("【状态】玩家有轻伤在身")
            if "疲惫" in player_state.status_effects:
                parts.append("【状态】玩家精疲力竭，难以施展")
            if "中毒" in player_state.status_effects:
                parts.append("【状态】玩家身中剧毒，毒性蔓延")

        return "\n".join(parts) if parts else ""
