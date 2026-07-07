from __future__ import annotations
import random
from .schemas import PlayerState, WorldState
from .llm.base_llm import BaseLLM


class ItemSystem:
    def __init__(self, llm: BaseLLM):
        self.llm = llm

    def generate_random_item(self, player_level: int, location: str = "") -> dict:
        prompt = f"""为玩家生成一个随机物品。玩家等级{player_level}，位置{location}。
输出JSON: {{"name":"名称","description":"描述","category":"weapon/armor/consumable/material/quest/special","rarity":"common/uncommon/rare/epic/legendary","value":价格}}"""
        return self.llm.chat_json(prompt, temperature=0.7)

    def use_item(self, player: PlayerState, item: dict) -> dict:
        effects = {}
        cat = item.get("category", "")
        if cat == "consumable":
            heal = min(30, player.stats.max_health - player.stats.health)
            player.stats.health += heal
            effects["health"] = heal
        elif cat == "weapon":
            bonus = item.get("stats", {}).get("strength", 0)
            player.stats.strength += bonus
            effects["strength"] = bonus
        return {"effects": effects, "item_name": item.get("name", "")}

    def get_inventory_summary(self, player: PlayerState) -> str:
        items = player.inventory.items
        if not items:
            return "背包空空如也。"
        lines = ["【背包】"]
        for item in items:
            lines.append(f"  {item.name} x{item.quantity} ({item.item_type})")
        lines.append(f"  金币: {player.social.gold}")
        return "\n".join(lines)


class SkillTree:
    def __init__(self, llm: BaseLLM):
        self.llm = llm
        self.unlocked_skills: list[str] = []
        self.skill_points: int = 0
        self.current_tree: dict = {}
        self.tree_type: str = ""

    def generate_tree(self, world_type: str, world_name: str) -> dict:
        prompt = f"""为这个世界类型生成一个技能树。

【世界类型】{world_type}
【世界名称】{world_name}

【要求】
1. 根据世界类型生成6个技能，从基础到高级
2. 每个技能有名称、描述、前置条件、效果
3. 符合世界设定（武侠有武技，修仙有法术，科幻有义体改造等）

【输出JSON格式】
{{
    "tree_name": "技能树名称",
    "skills": [
        {{
            "id": "skill_id",
            "name": "技能名",
            "description": "50字描述",
            "req_level": 0,
            "cost": 0,
            "effects": {{"stat": "值"}},
            "prerequisites": [],
            "flavor": "技能的风味描述"
        }}
    ]
}}"""
        response = self.llm.chat_json(prompt, temperature=0.7)
        self.current_tree = response
        self.tree_type = world_type
        return response

    def get_available_skills(self) -> list[dict]:
        skills = self.current_tree.get("skills", [])
        available = []
        for skill in skills:
            if skill["id"] not in self.unlocked_skills:
                prereqs_met = all(p in self.unlocked_skills for p in skill.get("prerequisites", []))
                available.append({
                    **skill,
                    "can_unlock": self.skill_points >= skill.get("cost", 0) and prereqs_met,
                    "prereqs_met": prereqs_met,
                })
        return available

    def unlock_skill(self, skill_id: str) -> dict:
        for skill in self.current_tree.get("skills", []):
            if skill["id"] == skill_id:
                if skill["id"] in self.unlocked_skills:
                    return {"success": False, "message": "已解锁"}
                if self.skill_points < skill.get("cost", 0):
                    return {"success": False, "message": f"技能点不足"}
                prereqs = skill.get("prerequisites", [])
                if not all(p in self.unlocked_skills for p in prereqs):
                    return {"success": False, "message": "前置技能未解锁"}
                self.skill_points -= skill.get("cost", 0)
                self.unlocked_skills.append(skill_id)
                return {"success": True, "skill": skill}
        return {"success": False, "message": "技能不存在"}

    def apply_effects(self, player: PlayerState):
        for skill in self.current_tree.get("skills", []):
            if skill["id"] in self.unlocked_skills:
                for stat, val in skill.get("effects", {}).items():
                    if hasattr(player.stats, stat):
                        setattr(player.stats, stat, getattr(player.stats, stat) + val)

    def add_points(self, amount: int):
        self.skill_points += amount

    def get_tree_display(self) -> str:
        tree_name = self.current_tree.get("tree_name", "技能树")
        lines = [f"【{tree_name}】 (技能点: {self.skill_points})"]
        for skill in self.current_tree.get("skills", []):
            status = "✅" if skill["id"] in self.unlocked_skills else "🔒"
            cost = skill.get("cost", 0)
            lines.append(f"  {status} {skill['name']} ({cost}点) - {skill.get('description', '')}")
        return "\n".join(lines) if len(lines) > 1 else "尚未生成技能树"
