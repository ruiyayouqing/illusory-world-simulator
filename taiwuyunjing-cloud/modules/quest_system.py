from __future__ import annotations
import random
from .schemas import PlayerState, WorldState
from .llm.base_llm import BaseLLM
from .prompt_utils import resolve_location_name  # [Bug] location code → display name


class Quest:
    def __init__(self, quest_id: str, title: str, description: str,
                 giver: str, quest_type: str = "main",
                 objectives: list[dict] = None, reward: dict = None,
                 deadline_day: int = 0):
        self.quest_id = quest_id
        self.title = title
        self.description = description
        self.giver = giver
        self.quest_type = quest_type
        self.objectives = objectives or []
        self.reward = reward or {}
        self.deadline_day = deadline_day
        self.status = "active"
        self.progress: list[str] = []
        self.completed_day: int | None = None

    def to_dict(self) -> dict:
        return {
            "quest_id": self.quest_id, "title": self.title,
            "description": self.description, "giver": self.giver,
            "quest_type": self.quest_type, "objectives": self.objectives,
            "reward": self.reward, "deadline_day": self.deadline_day,
            "status": self.status, "progress": self.progress,
            "completed_day": self.completed_day,
        }


    @classmethod
    def from_dict(cls, data: dict) -> "Quest":
        """[Bug] 反序列化 Quest 对象"""
        q = cls(
            data["quest_id"], data["title"], data["description"],
            data["giver"], data.get("quest_type", "side"),
            data.get("objectives", []), data.get("reward", {}),
            data.get("deadline_day", 0),
        )
        q.status = data.get("status", "active")
        q.progress = data.get("progress", [])
        q.completed_day = data.get("completed_day")
        return q


class QuestSystem:
    def __init__(self, llm: BaseLLM):
        self.llm = llm
        self.active_quests: list[Quest] = []
        self.completed_quests: list[Quest] = []
        self.failed_quests: list[Quest] = []
        self.quest_counter: int = 0
        self.revealed_quests: list[str] = []
        self.last_quest_check_location: str = ""

    def check_nearby_quests(self, player: PlayerState, world_state: WorldState,
                            location_description: str = "") -> list[dict]:
        if player.location == self.last_quest_check_location:
            return []
        self.last_quest_check_location = player.location

        prompt = f"""根据玩家当前位置和场景，判断是否有可接取的任务。

【玩家位置】{resolve_location_name(player.location, world_state)}  # [Bug] location code → display name
【场景描述】{location_description or '无特殊描述'}
【玩家信息】{player.name}，{player.age}岁，{player.social.position}
【当前时间】第{world_state.current_day}天 {world_state.current_time}
【天气】{world_state.weather}

【可能的任务来源】
- 城门口的悬赏告示
- 酒楼里的消息
- NPC主动搭话
- 宗门/帮派的任务板
- 路人求助
- 偶然发现

【输出JSON格式】
{{
    "has_quest": true/false,
    "quest_source": "任务来源描述（如：城门口贴着悬赏令）",
    "quest": {{
        "title": "任务标题",
        "description": "任务描述",
        "quest_type": "main/side/daily/hidden",
        "giver": "发布者",
        "objectives": [
            {{"text": "目标", "type": "kill/gather/talk/reach/collect", "target": "目标", "required": 1, "current": 0}}
        ],
        "reward": {{"gold": 50, "exp": 20, "items": []}},
        "deadline_days": 7
    }}
}}"""
        response = self.llm.chat_json(prompt, temperature=0.7)

        if response.get("has_quest") and response.get("quest"):
            quest_data = response["quest"]
            quest_data["giver"] = response.get("quest_source", "未知")
            return [{"source": response.get("quest_source", ""), "quest": quest_data}]
        return []

    def accept_quest(self, quest_data: dict, day: int) -> Quest:
        self.quest_counter += 1
        quest = Quest(
            quest_id=f"quest_{self.quest_counter}",
            title=quest_data.get("title", "未知任务"),
            description=quest_data.get("description", ""),
            giver=quest_data.get("giver", "未知"),
            quest_type=quest_data.get("quest_type", "side"),
            objectives=quest_data.get("objectives", []),
            reward=quest_data.get("reward", {}),
            deadline_day=day + quest_data.get("deadline_days", 7),
        )
        self.active_quests.append(quest)
        return quest

    def check_deadlines(self, world_state: WorldState) -> list[dict]:
        events = []
        for quest in list(self.active_quests):
            if quest.deadline_day > 0 and world_state.current_day > quest.deadline_day:
                quest.status = "failed"
                self.active_quests.remove(quest)
                self.failed_quests.append(quest)
                events.append({"type": "quest_failed", "quest": quest.to_dict()})
        return events

    def update_objective(self, quest_id: str, objective_index: int, amount: int = 1) -> dict:
        for quest in self.active_quests:
            if quest.quest_id == quest_id and objective_index < len(quest.objectives):
                obj = quest.objectives[objective_index]
                obj["current"] = min(obj["required"], obj.get("current", 0) + amount)
                if all(o["current"] >= o["required"] for o in quest.objectives):
                    quest.status = "completed"
                    quest.completed_day = 0
                    self.active_quests.remove(quest)
                    self.completed_quests.append(quest)
                    return {"completed": True, "quest": quest.to_dict()}
                return {"completed": False, "quest": quest.to_dict()}
        return {"completed": False, "message": "任务不存在"}

    def get_active_quests(self) -> list[dict]:
        return [q.to_dict() for q in self.active_quests]

    def get_quest_summary(self) -> str:
        if not self.active_quests:
            return "当前没有进行中的任务。"
        lines = ["【进行中的任务】"]
        for q in self.active_quests:
            deadline = f" (截止第{q.deadline_day}天)" if q.deadline_day > 0 else ""
            lines.append(f"  📋 {q.title}{deadline}")
            for obj in q.objectives:
                status = "✅" if obj["current"] >= obj["required"] else "⬜"
                lines.append(f"     {status} {obj['text']} ({obj.get('current', 0)}/{obj['required']})")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        # 序列化任务系统状态
        return {
            "active": [q.to_dict() for q in self.active_quests],
            "completed": [q.to_dict() for q in self.completed_quests],
            "failed": [q.to_dict() for q in self.failed_quests],
            "counter": self.quest_counter,
            "last_quest_check_location": self.last_quest_check_location,
        }

    def from_dict(self, data: dict):
        # [Bug] 恢复全部任务状态（active + completed + failed），避免任务履历丢失
        self.quest_counter = data.get("counter", 0)
        self.active_quests = [Quest.from_dict(qd) for qd in data.get("active", [])]
        self.completed_quests = [Quest.from_dict(qd) for qd in data.get("completed", [])]
        self.failed_quests = [Quest.from_dict(qd) for qd in data.get("failed", [])]
        self.last_quest_check_location = data.get("last_quest_check_location", "")
