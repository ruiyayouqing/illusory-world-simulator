from __future__ import annotations
import random
from .schemas import PlayerState, WorldState
from .llm.base_llm import BaseLLM


PERSONALITY_WHISPERS = {
    "力量型": {
        "trigger_tags": ["热血", "莽夫", "武者"],
        "responses": [
            "你的拳头在微微发痒，想给对面那家伙一拳。",
            "肌肉在叫嚣：冲上去！",
            "这棵树看起来很适合练拳...",
        ],
    },
    "智慧型": {
        "trigger_tags": ["学者", "谋士", "穿越者"],
        "responses": [
            "你的大脑在飞速运转，分析当前局势...",
            "作为一个穿越者，你知道这种情况下最明智的做法是...",
            "你在心里默默计算着风险与收益...",
        ],
    },
    "谨慎型": {
        "trigger_tags": ["谨慎", "稳重", "保守"],
        "responses": [
            "你的直觉在警告你：这里不太对劲。",
            "先观察，再行动。不要冲动。",
            "你下意识地摸了摸口袋，确认钱还在。",
        ],
    },
    "社交型": {
        "trigger_tags": ["话痨", "社交达人", "乐天派"],
        "responses": [
            "你忍不住想和旁边的人搭话。",
            "气氛有点沉闷，是时候活跃一下了。",
            "你在想：如果能和这个人交上朋友就好了。",
        ],
    },
    "阴暗型": {
        "trigger_tags": ["腹黑", "阴谋家", "冷酷"],
        "responses": [
            "你在心里冷笑：这些人真是太天真了。",
            "你注意到一个可以利用的弱点...",
            "如果不择手段的话，这件事其实很简单...",
        ],
    },
}


class BrainWhispers:
    def __init__(self, llm: BaseLLM):
        self.llm = llm
        self.whisper_history: list[dict] = []

    def generate_whispers(self, player: PlayerState, context: str,
                          world_state: WorldState) -> list[dict]:
        whispers = []

        for category, config in PERSONALITY_WHISPERS.items():
            if any(tag in player.tags for tag in config["trigger_tags"]):
                whisper = random.choice(config["responses"])
                whispers.append({
                    "category": category,
                    "text": whisper,
                    "source": "personality",
                })

        if player.stats.health < 30:
            whispers.append({
                "category": "pain",
                "text": "你的伤口在隐隐作痛，视线开始模糊...",
                "source": "status",
            })
        if player.stats.energy < 20:
            whispers.append({
                "category": "fatigue",
                "text": "困意如潮水般涌来，你快要撑不住了...",
                "source": "status",
            })
        if player.social.gold < 10:
            whispers.append({
                "category": "poverty",
                "text": "口袋里空空如也，你感到一阵绝望...",
                "source": "status",
            })

        if world_state.crisis_level >= 7:
            whispers.append({
                "category": "danger",
                "text": "危险的气息弥漫在空气中，你的警觉性提到了最高...",
                "source": "world",
            })

        prompt = f"""根据当前情境，生成1-2条角色内心独白（脑内碎碎念）。

【角色信息】
姓名: {player.name}, {player.age}岁
标签: {', '.join(player.tags)}
当前状态: {', '.join(player.status_effects) if player.status_effects else '正常'}

【当前情境】
{context}

【要求】
1. 用第一人称内心独白
2. 符合角色性格
3. 可以是吐槽、分析、恐惧、兴奋等
4. 50-80字

【输出JSON格式】
{{
    "whispers": [
        {{"category": "类型", "text": "独白内容"}}
    ]
}}"""
        response = self.llm.chat_json(prompt, temperature=0.8)
        for w in response.get("whispers", []):
            whispers.append({**w, "source": "llm"})

        random.shuffle(whispers)
        selected = whispers[:3]
        self.whisper_history.extend(selected)
        self.whisper_history = self.whisper_history[-100:]
        return selected

    def get_recent_whispers(self, n: int = 5) -> list[dict]:
        return self.whisper_history[-n:]

    def clear_whispers(self):
        self.whisper_history.clear()

    def to_dict(self) -> dict:
        # 序列化脑内碎碎念历史
        return {"history": self.whisper_history}

    def from_dict(self, data: dict):
        self.whisper_history = data.get("history", [])
