from __future__ import annotations
import random
from .schemas import PlayerState, WorldState
from .llm.base_llm import BaseLLM
from .prompt_utils import resolve_location_name  # [Bug] location code → display name


class DestinyRegret:
    def __init__(self, llm: BaseLLM):
        self.llm = llm
        self.missed_opportunities: list[dict] = []
        self.regrets_felt: list[dict] = []
        self.irreversible_choices: list[dict] = []

    def record_missed(self, opportunity_type: str, description: str,
                      day: int, npc_id: str = "", consequence: str = ""):
        entry = {
            "type": opportunity_type,
            "description": description,
            "day": day,
            "npc_id": npc_id,
            "consequence": consequence,
            "regretted": False,
        }
        self.missed_opportunities.append(entry)

    def record_irreversible(self, choice_type: str, description: str,
                            day: int, outcome: str):
        self.irreversible_choices.append({
            "type": choice_type,
            "description": description,
            "day": day,
            "outcome": outcome,
        })

    def check_regret(self, player: PlayerState, world_state: WorldState) -> dict | None:
        for opp in self.missed_opportunities:
            if opp["regretted"]:
                continue

            days_missed = world_state.current_day - opp["day"]

            if opp["type"] == "love" and days_missed > 60:
                if not any(r.favor > 80 for r in player.relations.values()):
                    opp["regretted"] = True
                    return self._generate_regret("love_lost", opp, player, world_state)

            if opp["type"] == "opportunity" and days_missed > 30:
                if player.social.gold < 200:
                    opp["regretted"] = True
                    return self._generate_regret("wealth_missed", opp, player, world_state)

            if opp["type"] == "danger_avoided" and days_missed > 20:
                opp["regretted"] = True
                return self._generate_regret("courage_questioned", opp, player, world_state)

        if player.age >= 40 and not self.missed_opportunities:
            return self._generate_regret("midlife_empty", None, player, world_state)

        if player.age >= 50 and player.social.gold < 300:
            return self._generate_regret("old_poverty", None, player, world_state)

        if player.age >= 60 and player.stats.health < 30:
            return self._generate_regret("old_weakness", None, player, world_state)

        return None

    def _generate_regret(self, regret_type: str, opp: dict,
                         player: PlayerState, world_state: WorldState) -> dict:
        prompt = f"""你是命运的旁观者。根据角色的人生经历，生成一段命运遗憾的叙事。

【角色信息】
姓名: {player.name}，{player.age}岁
标签: {', '.join(player.tags)}
位置: {resolve_location_name(player.location, world_state)}  # [Bug] location code → display name
金币: {player.social.gold} 声望: {player.social.reputation}

【遗憾类型】: {regret_type}
{"【错过的机会】: " + opp.get('description', '') if opp else ""}
{"【错过天数】: " + str(world_state.current_day - opp.get('day', 0)) + "天" if opp else ""}

【已做的不可逆选择】
{chr(10).join(['- ' + c['description'][:50] for c in self.irreversible_choices[-5:]]) or '无'}

【要求】
1. 用第二人称叙事，让玩家感到遗憾和感慨
2. 200-300字
3. 要有具体的细节和情感
4. 暗示"如果当初..."的对比
5. 结尾可以是释然、不甘、或感悟

直接输出叙事文本。"""
        narrative = self.llm.chat(prompt, temperature=0.85)

        return {
            "regret_type": regret_type,
            "narrative": narrative,
            "missed": opp,
            "effect": self._get_regret_effect(regret_type),
        }

    def _get_regret_effect(self, regret_type: str) -> str:
        effects = {
            "love_lost": "解锁'孤独'状态，社交选项受限",
            "wealth_missed": "解锁'贫困潦倒'状态，部分商店无法进入",
            "courage_questioned": "解锁'懦弱'标签，战斗选项减少",
            "midlife_empty": "解锁'虚度一生'状态，属性临时-3",
            "old_poverty": "解锁'晚景凄凉'状态，无法享受高级服务",
            "old_weakness": "解锁'风烛残年'状态，体力上限-30",
        }
        return effects.get(regret_type, "")

    def get_missed_summary(self) -> str:
        if not self.missed_opportunities:
            return "你没有错过任何机会。（也许吧）"
        lines = ["【你错过的那些事】"]
        for opp in self.missed_opportunities:
            status = "已释怀" if opp["regretted"] else "仍在心头"
            lines.append(f"  第{opp['day']}天: {opp['description'][:40]}... [{status}]")
        return "\n".join(lines)

    def get_irreversible_summary(self) -> str:
        if not self.irreversible_choices:
            return "你尚未做出不可逆的选择。"
        lines = ["【你走过的路】（无法回头）"]
        for c in self.irreversible_choices:
            lines.append(f"  第{c['day']}天: {c['description'][:40]}... → {c['outcome'][:30]}")
        return "\n".join(lines)
