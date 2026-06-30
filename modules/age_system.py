from __future__ import annotations
import random
from .schemas import PlayerState, WorldState, NPCState


TIME_SLOTS = ["清晨", "上午", "中午", "下午", "傍晚", "深夜"]
SEASON_NAMES = ["春季", "夏季", "秋季", "冬季"]
SEASON_EVENTS = {
    "春季": ["桃花盛开", "春雨绵绵", "万物复苏", "播种时节"],
    "夏季": ["烈日炎炎", "蝉鸣阵阵", "雷阵雨", "荷花满塘"],
    "秋季": ["秋高气爽", "枫叶如丹", "丰收季节", "凉风习习"],
    "冬季": ["寒风凛冽", "大雪纷飞", "炉火温暖", "梅花傲雪"],
}

AGE_MILESTONES = {
    18: {"title": "初来乍到", "desc": "你还是个不谙世事的年轻人", "effect": None},
    20: {"title": "弱冠之年", "desc": "你已不再是少年，该考虑人生方向了", "effect": "解锁'成年'标签"},
    22: {"title": "立业之年", "desc": "在这个世界待了几年，是时候闯出一番事业了", "effect": "声望获取+20%"},
    25: {"title": "而立之前", "desc": "你开始意识到时间不等人", "effect": "体力上限-5"},
    28: {"title": "娶妻生子", "desc": "村里人开始催你成家了", "effect": "可触发婚姻事件"},
    30: {"title": "而立之年", "desc": "三十而立，你终于在这个世界站稳了脚跟", "effect": "智力+2，体力上限-10"},
    35: {"title": "壮年", "desc": "你正值壮年，但也开始感到岁月的重量", "effect": "力量+1，敏捷-1"},
    40: {"title": "不惑之年", "desc": "你对这个世界有了更深的理解", "effect": "智力+2，力量-1，敏捷-1"},
    45: {"title": "知天命前", "desc": "你开始思考人生的意义", "effect": "解锁'人生感悟'选项"},
    50: {"title": "知天命", "desc": "五十而知天命，你回顾这一生，有遗憾也有满足", "effect": "所有属性调整"},
    55: {"title": "天命之年", "desc": "你已经是村里的老人了", "effect": "体力上限-15"},
    60: {"title": "花甲之年", "desc": "人生七十古来稀，你已经走过了大半生", "effect": "体力上限-20，智力+3"},
}

LIFE_GOALS = {
    "wealthy": {"name": "富甲一方", "desc": "攒够10000金币", "target": 10000, "type": "gold"},
    "famous": {"name": "名扬天下", "desc": "声望达到100", "target": 100, "type": "reputation"},
    "powerful": {"name": "武艺超群", "desc": "力量达到30", "target": 30, "type": "strength"},
    "wise": {"name": "学富五车", "desc": "智力达到30", "target": 30, "type": "intelligence"},
    "love": {"name": "佳人相伴", "desc": "与一人好感度达到100", "target": 100, "type": "favor"},
    "explorer": {"name": "走遍天下", "desc": "去过5个以上地点", "target": 5, "type": "locations"},
    "survivor": {"name": "长命百岁", "desc": "活到60岁", "target": 60, "type": "age"},
}


class AgeSystem:
    def __init__(self):
        self.events_log: list[dict] = []
        self.visited_locations: set[str] = set()
        self.life_goal: str | None = None
        self.age_milestones_hit: list[int] = []

    def set_life_goal(self, goal_type: str):
        if goal_type in LIFE_GOALS:
            self.life_goal = goal_type

    def get_available_goals(self) -> list[dict]:
        return [{"id": k, **v} for k, v in LIFE_GOALS.items()]

    def check_life_goal(self, player: PlayerState) -> dict | None:
        if not self.life_goal:
            return None
        goal = LIFE_GOALS.get(self.life_goal)
        if not goal:
            return None

        current = 0
        if goal["type"] == "gold":
            current = player.social.gold
        elif goal["type"] == "reputation":
            current = player.social.reputation
        elif goal["type"] == "strength":
            current = player.stats.strength
        elif goal["type"] == "intelligence":
            current = player.stats.intelligence
        elif goal["type"] == "favor":
            current = max((r.favor for r in player.relations.values()), default=0)
        elif goal["type"] == "locations":
            current = len(self.visited_locations)
        elif goal["type"] == "age":
            current = player.age

        progress = min(1.0, current / goal["target"]) if goal["target"] > 0 else 0
        achieved = current >= goal["target"]

        return {
            "goal_name": goal["name"],
            "goal_desc": goal["desc"],
            "current": current,
            "target": goal["target"],
            "progress": round(progress * 100),
            "achieved": achieved,
        }

    def advance_time(self, world_state: WorldState, hours: int = 1) -> dict:
        result = {"time_changed": False, "day_changed": False, "season_changed": False,
                  "new_day": False, "new_season": False, "events": [],
                  "age_events": [], "goal_progress": None}

        for _ in range(hours):
            old_time = world_state.current_time
            idx = TIME_SLOTS.index(world_state.current_time) if world_state.current_time in TIME_SLOTS else 0
            next_idx = (idx + 1) % len(TIME_SLOTS)
            if next_idx == 0:
                world_state.current_day += 1
                world_state.current_day_of_month += 1
                if world_state.current_day_of_month > 30:
                    world_state.current_day_of_month = 1
                    world_state.current_month += 1
                    if world_state.current_month > 12:
                        world_state.current_month = 1
                        world_state.current_year += 1
                        if world_state.era_name:
                            world_state.era_year += 1
                result["day_changed"] = True
                result["new_day"] = True
                result["events"].append(f"新的一天开始了：{world_state.get_full_date()}")
            world_state.current_time = TIME_SLOTS[next_idx]
            result["time_changed"] = True

            new_season = self._get_season(world_state.current_day)
            if new_season != world_state.season:
                world_state.season = new_season
                result["season_changed"] = True
                result["new_season"] = True
                result["events"].append(f"季节变化：{new_season}来了")

            weather = self._roll_weather(new_season)
            if weather != world_state.weather:
                world_state.weather = weather
                result["events"].append(f"天气变化：{weather}")

        return result

    def age_player(self, player: PlayerState, world_state: WorldState) -> dict:
        result = {"aged": False, "milestone": None, "events": [],
                  "time_pressure": None, "stat_changes": []}

        total_days = world_state.current_day
        years_passed = total_days // 365
        new_age = 18 + years_passed

        if new_age > player.age:
            player.age = new_age
            result["aged"] = True
            result["events"].append(f"你又老了一岁，现在{player.age}岁了")

            if player.age in AGE_MILESTONES and player.age not in self.age_milestones_hit:
                milestone = AGE_MILESTONES[player.age]
                result["milestone"] = milestone
                result["events"].append(f"【{milestone['title']}】{milestone['desc']}")
                self.age_milestones_hit.append(player.age)

            stat_changes = self._apply_age_stats(player)
            result["stat_changes"] = stat_changes

            if player.age >= 30:
                result["time_pressure"] = {
                    "level": "mild",
                    "message": "你开始感到时间的压力，不能再漫无目的地度日了",
                }
            if player.age >= 40:
                result["time_pressure"] = {
                    "level": "moderate",
                    "message": "岁月不饶人，你的体力在下降，该抓紧时间完成目标了",
                }
            if player.age >= 50:
                result["time_pressure"] = {
                    "level": "severe",
                    "message": "人生过半，如果还没有成就，恐怕就来不及了...",
                }

        return result

    def age_npc(self, npc: NPCState, days: int = 1) -> dict:
        result = {"events": []}
        years_passed = days // 365
        if years_passed > 0:
            npc.age += years_passed
            result["events"].append(f"{npc.name}也老了一岁，现在{npc.age}岁了")
        return result

    def record_location(self, location: str):
        self.visited_locations.add(location)

    def get_age_narrative(self, player: PlayerState, milestone: dict) -> str:
        return f"【{milestone['title']}】{milestone['desc']}"

    def get_time_pressure_narrative(self, pressure: dict) -> str:
        if not pressure:
            return ""
        level = pressure.get("level", "")
        if level == "severe":
            return "\n⚠️ 【时间紧迫】" + pressure["message"]
        elif level == "moderate":
            return "\n⏳ 【岁月催人】" + pressure["message"]
        elif level == "mild":
            return "\n🕐 【初感压力】" + pressure["message"]
        return ""

    def get_time_description(self, world_state: WorldState) -> str:
        time_desc = {
            "清晨": "天刚蒙蒙亮，公鸡打鸣",
            "上午": "阳光正好，村里人开始忙碌",
            "中午": "日头正毒，该吃午饭了",
            "下午": "午后微风，适合在树下乘凉",
            "傍晚": "夕阳西下，炊烟袅袅",
            "深夜": "月黑风高，万籁俱寂",
        }
        return time_desc.get(world_state.current_time, "")

    def _get_season(self, day: int) -> str:
        day_in_year = day % 120
        if day_in_year < 30: return "春季"
        elif day_in_year < 60: return "夏季"
        elif day_in_year < 90: return "秋季"
        return "冬季"

    def _roll_weather(self, season: str) -> str:
        weights = {
            "春季": {"晴朗": 40, "多云": 30, "小雨": 20, "大雨": 10},
            "夏季": {"晴朗": 30, "多云": 20, "雷阵雨": 30, "酷热": 20},
            "秋季": {"晴朗": 50, "多云": 30, "小雨": 15, "大风": 5},
            "冬季": {"晴朗": 30, "多云": 30, "小雪": 25, "大雪": 15},
        }
        w = weights.get(season, {"晴朗": 100})
        return random.choices(list(w.keys()), weights=list(w.values()))[0]

    def _apply_age_stats(self, player: PlayerState) -> list[str]:
        changes = []
        age = player.age
        if age == 30:
            player.stats.strength = max(1, player.stats.strength - 1)
            changes.append("力量-1（体力开始下降）")
        if age == 35:
            player.stats.agility = max(1, player.stats.agility - 1)
            changes.append("敏捷-1（反应变慢）")
        if age == 40:
            player.stats.strength = max(1, player.stats.strength - 1)
            player.stats.agility = max(1, player.stats.agility - 1)
            changes.append("力量-1, 敏捷-1（身体机能衰退）")
        if age == 25:
            player.stats.intelligence += 1
            changes.append("智力+1（阅历增长）")
        if age == 35:
            player.stats.luck += 1
            changes.append("幸运+1（福至心灵）")
        return changes

    def to_dict(self) -> dict:
        # 序列化年龄系统状态（visited_locations 是 set，需转 list）
        return {
            "milestones_hit": self.age_milestones_hit,
            "visited_locations": list(self.visited_locations),
            "life_goal": self.life_goal,
        }

    def from_dict(self, data: dict):
        self.age_milestones_hit = data.get("milestones_hit", [])
        self.visited_locations = set(data.get("visited_locations", []))
        self.life_goal = data.get("life_goal")
