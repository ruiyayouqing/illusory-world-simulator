from __future__ import annotations
import random
from .schemas import PlayerState, WorldState
from .llm.base_llm import BaseLLM
from .prompt_utils import resolve_location_name  # [Bug] location code → display name


class DeathSystem:
    def __init__(self, llm: BaseLLM):
        self.llm = llm
        self.death_count: int = 0
        self.death_history: list[dict] = []
        self.reincarnation_data: dict | None = None

    def check_death(self, player: PlayerState, world_state: WorldState) -> dict | None:
        if player.stats.health <= 0:
            return self._generate_death(player, world_state, "重伤不治")
        if player.stats.energy <= 0 and player.stats.health < 20:
            return self._generate_death(player, world_state, "体力耗竭，倒地不起")

        max_age = getattr(player, 'max_age', 80)
        old_age_threshold = max_age - 10
        death_age_threshold = max_age

        if player.age >= death_age_threshold and random.random() < 0.1:
            return self._generate_death(player, world_state, "寿终正寝")
        if player.age >= old_age_threshold and player.stats.health < 30 and random.random() < 0.05:
            return self._generate_death(player, world_state, "年老体衰")
        return None

    def trigger_suicide(self, player: PlayerState, world_state: WorldState) -> dict:
        return self._generate_death(player, world_state, "自尽")

    def _generate_death(self, player: PlayerState, world_state: WorldState,
                        cause: str) -> dict:
        self.death_count += 1

        prompt = f"""为角色的死亡生成一段叙事。

【角色信息】
姓名: {player.name}，{player.age}岁
标签: {', '.join(player.tags)}
位置: {resolve_location_name(player.location, world_state)}  # [Bug] location code → display name
金币: {player.social.gold} 声望: {player.social.reputation}

【死亡原因】: {cause}
【世界日期】: 第{world_state.current_day}天

【要求】
1. 用第三人称描写死亡场景
2. 200-300字
3. 要有情感：可能是悲壮、遗憾、释然、或意外
4. 回顾这一生的某个闪光点
5. 结尾留下悬念或感悟

直接输出叙事文本。"""
        narrative = self.llm.chat(prompt, temperature=0.85)

        death_record = {
            "name": player.name,
            "age": player.age,
            "cause": cause,
            "day": world_state.current_day,
            "location": player.location,
            "gold": player.social.gold,
            "reputation": player.social.reputation,
            "narrative": narrative,
        }
        self.death_history.append(death_record)

        return {
            "died": True,
            "cause": cause,
            "narrative": narrative,
            "death_count": self.death_count,
            "options": self._get_death_options(player, world_state),
        }

    def _get_death_options(self, player: PlayerState, world_state: WorldState) -> list[dict]:
        options = [
            {"id": "A", "text": "读取存档，回到过去", "type": "reload",
             "description": "从最近的存档点重新开始"},
            {"id": "B", "text": "再次降临这个世界", "type": "reincarnate",
             "description": "以新身份重生在同一世界，继承部分记忆"},
            {"id": "C", "text": "开始全新世界", "type": "new_world",
             "description": "放弃这个世界，创造一个全新的世界"},
        ]

        if self.death_count == 1:
            options[1]["text"] = "再次降临（首次重生，保留全部记忆）"
        elif self.death_count >= 3:
            options[1]["text"] = "再次降临（第{}次，你会带着所有轮回的记忆）".format(self.death_count)

        return options

    def prepare_reincarnation(self, old_player: PlayerState,
                              world_state: WorldState) -> dict:
        self.reincarnation_data = {
            "previous_life": {
                "name": old_player.name,
                "age": old_player.age,
                "cause_of_death": self.death_history[-1]["cause"] if self.death_history else "未知",
                "achievements": self._extract_achievements(old_player),
                "regrets": [],
                "memories": old_player.memory.short_term[-5:],
            },
            "reincarnation_number": self.death_count,
            "world_knowledge": world_state.event_history_summary[:500] if world_state.event_history_summary else "",
            "inherited_tags": ["转世者", "前世记忆"],
        }

        return self.reincarnation_data

    def _extract_achievements(self, player: PlayerState) -> list[str]:
        achievements = []
        if player.social.gold >= 1000:
            achievements.append("积累了大量财富")
        if player.social.reputation >= 50:
            achievements.append("声名远播")
        if player.stats.strength >= 15:
            achievements.append("武艺高强")
        if player.stats.intelligence >= 15:
            achievements.append("学识渊博")
        if any(r.favor >= 80 for r in player.relations.values()):
            achievements.append("收获了真挚的感情")
        if not achievements:
            achievements.append("度过了平凡的一生")
        return achievements

    def generate_reincarnation_narrative(self, reincarnation_data: dict) -> str:
        prev = reincarnation_data.get("previous_life", {})
        num = reincarnation_data.get("reincarnation_number", 1)

        prompt = f"""为角色的转世重生生成一段叙事。

【前世信息】
姓名: {prev.get('name', '未知')}
死亡年龄: {prev.get('age', '?')}岁
死因: {prev.get('cause_of_death', '未知')}
成就: {', '.join(prev.get('achievements', []))}

【转世次数】: 第{num}次

【要求】
1. 用第一人称"我"的视角
2. 描写从死亡到重生的过程
3. 前世记忆如何保留（片段式、模糊的、或清晰的）
4. 对新人生的期待或恐惧
5. 200-300字

直接输出叙事文本。"""
        return self.llm.chat(prompt, temperature=0.9)

    def get_death_stats(self) -> dict:
        return {
            "death_count": self.death_count,
            "death_history": [
                {"name": d["name"], "age": d["age"], "cause": d["cause"],
                 "day": d["day"], "location": d["location"]}
                for d in self.death_history
            ],
        }

    def to_dict(self) -> dict:
        # 序列化死亡系统状态
        return {
            "death_count": self.death_count,
            "death_history": self.death_history,
        }

    def from_dict(self, data: dict):
        self.death_count = data.get("death_count", 0)
        self.death_history = data.get("death_history", [])
