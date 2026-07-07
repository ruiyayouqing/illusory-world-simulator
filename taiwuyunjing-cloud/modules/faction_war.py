from __future__ import annotations
import random
from .schemas import PlayerState, WorldState
from .llm.base_llm import BaseLLM
from .prompt_utils import resolve_location_name  # [Bug] location code → display name


class PlayerMemoir:
    def __init__(self, llm: BaseLLM):
        self.llm = llm
        self.entries: list[dict] = []

    def add_entry(self, day: int, title: str, content: str,
                  importance: str = "normal"):
        self.entries.append({
            "day": day,
            "title": title,
            "content": content,
            "importance": importance,
        })

    def record_event(self, player: PlayerState, event_type: str,
                     description: str, day: int):
        importance = "normal"
        if event_type in ["milestone", "death", "marriage", "war"]:
            importance = "high"
        elif event_type in ["skill_up", "item_get"]:
            importance = "low"

        self.add_entry(day, f"第{day}天 - {event_type}", description, importance)

    def generate_memoir_chapter(self, player: PlayerState,
                                start_day: int, end_day: int) -> str:
        relevant = [e for e in self.entries if start_day <= e["day"] <= end_day]
        if not relevant:
            return ""

        entries_text = "\n".join([
            f"- [{e['importance']}] {e['title']}: {e['content'][:100]}"
            for e in relevant
        ])

        prompt = f"""将以下事件编写成回忆录章节。

【角色信息】
姓名: {player.name}，{player.age}岁
标签: {', '.join(player.tags)}

【事件记录】（第{start_day}天到第{end_day}天）
{entries_text}

【要求】
1. 用第一人称回忆录风格
2. 200-300字
3. 有感情色彩，可以是怀念、遗憾、骄傲等
4. 标注时间

直接输出文本。"""
        return self.llm.chat(prompt, temperature=0.85)

    def get_full_memoir(self, player: PlayerState) -> str:
        if not self.entries:
            return "你的回忆录还是空白的..."

        years = {}
        for entry in self.entries:
            year = (entry["day"] // 365) + 18
            if year not in years:
                years[year] = []
            years[year].append(entry)

        chapters = []
        for year in sorted(years.keys()):
            chapter = self.generate_memoir_chapter(
                player, years[year][0]["day"], years[year][-1]["day"]
            )
            if chapter:
                chapters.append(f"## 第{year}岁\n{chapter}")

        return "\n\n".join(chapters) if chapters else "你的回忆录还是空白的..."

    def get_memoir_stats(self) -> dict:
        return {
            "total_entries": len(self.entries),
            "high_importance": len([e for e in self.entries if e["importance"] == "high"]),
            "first_day": self.entries[0]["day"] if self.entries else 0,
            "last_day": self.entries[-1]["day"] if self.entries else 0,
        }


class NpcFavorEvents:
    def __init__(self, llm: BaseLLM):
        self.llm = llm
        self.triggered_events: list[dict] = []

    def check_favor_triggers(self, player: PlayerState,
                             world_state: WorldState) -> list[dict]:
        events = []
        for npc_id, relation in player.relations.items():
            favor = relation.favor

            if favor >= 90 and "confession" not in [e["type"] for e in self.triggered_events if e.get("npc_id") == npc_id]:
                events.append({
                    "npc_id": npc_id,
                    "type": "confession",
                    "trigger": "好感度达到90",
                    "description": f"{npc_id}对你的好感度达到了顶峰，可能会有重要剧情...",
                })
                self.triggered_events.append({"npc_id": npc_id, "type": "confession"})

            if favor <= 10 and "rivalry" not in [e["type"] for e in self.triggered_events if e.get("npc_id") == npc_id]:
                events.append({
                    "npc_id": npc_id,
                    "type": "rivalry",
                    "trigger": "好感度降到10",
                    "description": f"{npc_id}对你产生了敌意，可能会有冲突...",
                })
                self.triggered_events.append({"npc_id": npc_id, "type": "rivalry"})

        return events

    def generate_favor_event(self, npc_id: str, event_type: str,
                             player: PlayerState, world_state=None) -> dict:
        npc_relation = player.relations.get(npc_id)
        if not npc_relation:
            return {}

        prompt = f"""根据NPC与玩家的关系，生成一个好感度触发事件。

【NPC信息】
ID: {npc_id}
好感度: {npc_relation.favor}/100
关系: {npc_relation.relation_type}

【玩家信息】
姓名: {player.name}
位置: {resolve_location_name(player.location, world_state)}

【事件类型】
{event_type}

【输出JSON格式】
{{
    "event_title": "事件标题",
    "event_description": "事件描述（200字）",
    "player_choices": [
        {{"choice": "选项描述", "favor_change": 10, "consequence": "后果"}}
    ]
}}

只输出JSON。"""
        return self.llm.chat_json(prompt, temperature=0.7)


class FactionWars:
    def __init__(self, llm: BaseLLM):
        self.llm = llm
        self.active_wars: list[dict] = []
        self.war_history: list[dict] = []

    def check_war_triggers(self, world_state: WorldState) -> list[dict]:
        events = []
        factions = world_state.factions

        for name, faction in factions.items():
            for enemy_name in getattr(faction, 'enemies', []):
                if enemy_name in factions:
                    enemy = factions[enemy_name]
                    tension = (faction.power + enemy.power) / 200
                    if tension > 0.7 and random.random() < tension * 0.3:
                        war = {
                            "faction_a": name,
                            "faction_b": enemy_name,
                            "start_day": world_state.current_day,
                            "reason": f"{name}与{enemy_name}的矛盾激化",
                        }
                        if not any(w["faction_a"] == name and w["faction_b"] == enemy_name
                                   for w in self.active_wars):
                            self.active_wars.append(war)
                            events.append(war)
        return events

    def generate_war_event(self, war: dict, world_state: WorldState) -> dict:
        prompt = f"""为两个势力之间的战争生成一个事件。

【战争信息】
交战双方: {war['faction_a']} vs {war['faction_b']}
原因: {war['reason']}
开始时间: 第{war['start_day']}天

【世界状态】
当前日期: 第{world_state.current_day}天
危机等级: {world_state.crisis_level}/10

【输出JSON格式】
{{
    "event_type": "战争事件",
    "title": "事件标题",
    "description": "200字的事件描述",
    "impact_level": 1到10,
    "affected_locations": ["受影响的地点"],
    "player_relevance": "与玩家的关联",
    "choices": ["玩家可选择的行动"]
}}

只输出JSON。"""
        return self.llm.chat_json(prompt, temperature=0.8)

    def get_war_status(self) -> str:
        if not self.active_wars:
            return "目前没有战争。"
        lines = ["【战争状态】"]
        for war in self.active_wars:
            lines.append(f"  {war['faction_a']} vs {war['faction_b']} (始于第{war['start_day']}天)")
        return "\n".join(lines)
