from __future__ import annotations
import random
from .schemas import WorldState, PlayerState
from .llm.base_llm import BaseLLM


class War:
    def __init__(self, faction_a: str, faction_b: str, start_day: int,
                 reason: str, war_type: str = "territory"):
        self.faction_a = faction_a
        self.faction_b = faction_b
        self.start_day = start_day
        self.reason = reason
        self.war_type = war_type
        self.phase = "brewing"
        self.battles: list[dict] = []
        self.casualties: dict[str, int] = {faction_a: 0, faction_b: 0}
        self.territory_control: dict[str, str] = {}
        self.player_side: str | None = None
        self.player_participated: bool = False
        self.outcome: str = ""
        self.end_day: int | None = None

    def to_dict(self) -> dict:
        # [Bug] battles 必须完整序列化（含胜负/伤亡/日期），否则读档后战斗细节全部丢失
        return {
            "faction_a": self.faction_a,
            "faction_b": self.faction_b,
            "start_day": self.start_day,
            "reason": self.reason,
            "war_type": self.war_type,
            "phase": self.phase,
            "battles": list(self.battles),
            "casualties": dict(self.casualties),
            "territory_control": dict(self.territory_control),
            "player_side": self.player_side,
            "player_participated": self.player_participated,
            "outcome": self.outcome,
            "end_day": self.end_day,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "War":
        """[Bug] 反序列化 War 对象，使 active_wars 能在读档后恢复"""
        war = cls(
            data["faction_a"], data["faction_b"], data["start_day"],
            data["reason"], data.get("war_type", "territory"),
        )
        war.phase = data.get("phase", "brewing")
        war.battles = list(data.get("battles", []))
        war.casualties = dict(data.get("casualties", {war.faction_a: 0, war.faction_b: 0}))
        war.territory_control = dict(data.get("territory_control", {}))
        war.player_side = data.get("player_side")
        war.player_participated = data.get("player_participated", False)
        war.outcome = data.get("outcome", "")
        war.end_day = data.get("end_day")
        return war


class FactionWars:
    def __init__(self, llm: BaseLLM):
        self.llm = llm
        self.active_wars: list[War] = []
        self.history: list[dict] = []
        self.last_war_check_day: int = 0

    def check_war_triggers(self, world_state: WorldState) -> list[dict]:
        events = []
        days_since = world_state.current_day - self.last_war_check_day

        if days_since < 3:
            return events

        self.last_war_check_day = world_state.current_day

        if len(self.active_wars) >= 2:
            return events

        prompt = f"""分析当前世界局势，判断是否有势力应该爆发冲突。

【世界状态】
日期: 第{world_state.current_day}天
危机等级: {world_state.crisis_level}/10
季节: {world_state.season}

【现有活跃战争】
{len(self.active_wars)}场

【输出JSON格式】
{{
    "should_trigger_war": true/false,
    "faction_a": "势力A",
    "faction_b": "势力B",
    "reason": "战争原因",
    "war_type": "territory/economic/ideological/religious",
    "tension_level": 1到10
}}"""
        response = self.llm.chat_json(prompt, temperature=0.7)

        if response.get("should_trigger_war") and response.get("faction_a") and response.get("faction_b"):
            war = War(
                faction_a=response["faction_a"],
                faction_b=response["faction_b"],
                start_day=world_state.current_day,
                reason=response.get("reason", "势力冲突"),
                war_type=response.get("war_type", "territory"),
            )
            self.active_wars.append(war)

            event = self._generate_war_event(war, "declaration", world_state)
            events.append(event)

        return events

    def advance_war(self, war: War, world_state: WorldState) -> dict:
        if war.phase == "brewing":
            war.phase = "escalation"
            return self._generate_war_event(war, "escalation", world_state)

        elif war.phase == "escalation":
            if len(war.battles) >= 1 or world_state.current_day - war.start_day >= 5:
                war.phase = "active"
                return self._generate_war_event(war, "first_battle", world_state)

        elif war.phase == "active":
            if random.random() < 0.3 or len(war.battles) >= 3:
                battle = self._generate_battle(war, world_state)
                war.battles.append(battle)

                if war.casualties[war.faction_a] > 50 or war.casualties[war.faction_b] > 50:
                    war.phase = "ceasefire"
                    return self._generate_war_event(war, "ceasefire", world_state)

                if random.random() < 0.2:
                    war.phase = "ending"
                    return self._generate_war_event(war, "climax", world_state)

        elif war.phase == "ceasefire":
            if random.random() < 0.1:
                war.phase = "ending"

        elif war.phase == "ending":
            winner = war.faction_a if war.casualties[war.faction_b] > war.casualties[war.faction_a] else war.faction_b
            war.outcome = f"{winner}获胜"
            war.end_day = world_state.current_day
            self.history.append(war.to_dict())
            self.active_wars.remove(war)
            return self._generate_war_event(war, "resolution", world_state)

        return {}

    def _generate_war_event(self, war: War, event_type: str,
                            world_state: WorldState) -> dict:
        prompt = f"""为这场战争生成一个事件。

【战争信息】
{war.faction_a} vs {war.faction_b}
原因: {war.reason}
类型: {war.war_type}
阶段: {war.phase}
已发生{len(war.battles)}场战斗

【事件类型】: {event_type}
- declaration: 宣战
- escalation: 局势升级
- first_battle: 第一场战斗
- ceasefire: 停火谈判
- climax: 决战
- resolution: 战争结束

【输出JSON格式】
{{
    "title": "事件标题",
    "description": "200字的事件描述",
    "impact_level": 1到10,
    "player_relevance": "与玩家的关联（地点、人物、利益）",
    "casualties_a": 伤亡数,
    "casualties_b": 伤亡数
}}

只输出JSON。"""
        response = self.llm.chat_json(prompt, temperature=0.8)

        if event_type in ["first_battle", "climax"]:
            a_cas = response.get("casualties_a", random.randint(5, 20))
            b_cas = response.get("casualties_b", random.randint(5, 20))
            war.casualties[war.faction_a] += a_cas
            war.casualties[war.faction_b] += b_cas

        return {
            "event_type": "war",
            "war_phase": event_type,
            "faction_a": war.faction_a,
            "faction_b": war.faction_b,
            "title": response.get("title", f"{war.faction_a}与{war.faction_b}的冲突"),
            "description": response.get("description", ""),
            "impact_level": response.get("impact_level", 5),
            "player_relevance": response.get("player_relevance", ""),
            "casualties": war.casualties.copy(),
        }

    def _generate_battle(self, war: War, world_state: WorldState) -> dict:
        a_power = random.randint(20, 80)
        b_power = random.randint(20, 80)
        winner = war.faction_a if a_power > b_power else war.faction_b
        loser = war.faction_b if winner == war.faction_a else war.faction_a

        a_cas = random.randint(3, 15)
        b_cas = random.randint(3, 15)
        if winner == war.faction_a:
            b_cas += random.randint(2, 8)
        else:
            a_cas += random.randint(2, 8)

        return {
            "day": world_state.current_day,
            "winner": winner,
            "loser": loser,
            "attacker_power": a_power,
            "defender_power": b_power,
            "casualties": {war.faction_a: a_cas, war.faction_b: b_cas},
        }

    def get_war_status(self) -> str:
        if not self.active_wars:
            return "目前没有战争。天下太平。"
        lines = ["【战争状态】"]
        for war in self.active_wars:
            phase_cn = {"brewing": "酝酿中", "escalation": "升级中", "active": "进行中",
                        "ceasefire": "停火中", "ending": "即将结束"}
            lines.append(f"  ⚔️ {war.faction_a} vs {war.faction_b}")
            lines.append(f"     阶段: {phase_cn.get(war.phase, war.phase)} | 战斗: {len(war.battles)}场")
            lines.append(f"     伤亡: {war.faction_a}:{war.casualties[war.faction_a]} | {war.faction_b}:{war.casualties[war.faction_b]}")
        return "\n".join(lines)

    def get_war_history(self) -> str:
        if not self.history:
            return "没有战争历史。"
        lines = ["【战争历史】"]
        for w in self.history:
            lines.append(f"  第{w['start_day']}-{w.get('end_day', '?')}天: {w['faction_a']} vs {w['faction_b']} → {w['outcome']}")
        return "\n".join(lines)

    def get_player_wars(self, player: PlayerState) -> list[dict]:
        relevant = []
        for war in self.active_wars:
            if war.player_side:
                relevant.append({
                    "war": f"{war.faction_a} vs {war.faction_b}",
                    "side": war.player_side,
                    "phase": war.phase,
                })
        return relevant

    def to_dict(self) -> dict:
        # 序列化战争状态（active_wars 仅保存摘要用于检视）
        return {
            "active_wars": [w.to_dict() for w in self.active_wars],
            "history": self.history,
            "last_check_day": self.last_war_check_day,
        }

    def from_dict(self, data: dict):
        # [Bug] 恢复 active_wars——War.from_dict() 现在可以完整重建 War 对象
        self.active_wars = [War.from_dict(wd) for wd in data.get("active_wars", [])]
        self.history = data.get("history", [])
        self.last_war_check_day = data.get("last_check_day", 0)
