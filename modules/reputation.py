from __future__ import annotations
from .schemas import PlayerState, WorldState


class ReputationSystem:
    def __init__(self):
        self.faction_reputation: dict[str, int] = {}
        self.wanted_level: int = 0
        self.wanted_by: list[str] = []
        self.crime_history: list[dict] = []

    def init_factions(self, factions: dict):
        for name in factions:
            if name not in self.faction_reputation:
                self.faction_reputation[name] = 0

    def change_reputation(self, faction: str, amount: int, reason: str = ""):
        if faction not in self.faction_reputation:
            self.faction_reputation[faction] = 0
        self.faction_reputation[faction] = max(-100, min(100,
            self.faction_reputation[faction] + amount))

    def add_crime(self, crime_type: str, description: str,
                  faction: str = "", day: int = 0):
        self.crime_history.append({
            "type": crime_type,
            "description": description,
            "faction": faction,
            "day": day,
        })
        if faction:
            self.change_reputation(faction, -20, f"犯罪: {description}")
        self.wanted_level = min(5, self.wanted_level + 1)
        if faction and faction not in self.wanted_by:
            self.wanted_by.append(faction)

    def reduce_wanted(self, amount: int = 1):
        self.wanted_level = max(0, self.wanted_level - amount)
        if self.wanted_level == 0:
            self.wanted_by.clear()

    def get_faction_status(self, faction: str) -> str:
        rep = self.faction_reputation.get(faction, 0)
        if rep >= 80: return "崇敬"
        elif rep >= 50: return "友好"
        elif rep >= 20: return "中立偏善"
        elif rep >= -20: return "中立"
        elif rep >= -50: return "冷淡"
        elif rep >= -80: return "敌对"
        else: return "仇恨"

    def get_reputation_display(self) -> str:
        if not self.faction_reputation:
            return "无势力关系"
        lines = ["【势力声望】"]
        for faction, rep in sorted(self.faction_reputation.items(), key=lambda x: -x[1]):
            status = self.get_faction_status(faction)
            bar = "+" * max(0, rep // 10) + "-" * max(0, -rep // 10)
            lines.append(f"  {faction}: {status} [{bar}] {rep}")
        if self.wanted_level > 0:
            lines.append(f"\n  ⚠️ 通缉等级: {'★' * self.wanted_level}{'☆' * (5 - self.wanted_level)}")
            lines.append(f"  通缉方: {', '.join(self.wanted_by)}")
        return "\n".join(lines)

    def get_wanted_effects(self) -> dict:
        return {
            "level": self.wanted_level,
            "shop_price_modifier": 1.0 + self.wanted_level * 0.1,
            "npc_fear_modifier": self.wanted_level * -5,
            "guard_attention": self.wanted_level >= 2,
        }

    def to_dict(self) -> dict:
        # 序列化声望/通缉系统状态
        return {
            "faction_reputation": self.faction_reputation,
            "wanted_level": self.wanted_level,
            "wanted_by": self.wanted_by,
            "crime_history": self.crime_history,
        }

    def from_dict(self, data: dict):
        self.faction_reputation = data.get("faction_reputation", {})
        self.wanted_level = data.get("wanted_level", 0)
        self.wanted_by = data.get("wanted_by", [])
        self.crime_history = data.get("crime_history", [])
