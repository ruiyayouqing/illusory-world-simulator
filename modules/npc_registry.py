from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any
from collections import OrderedDict

logger = logging.getLogger("chronoverse")


class NpcImportance(str, Enum):
    WORLD = "world"
    LOCAL = "local"
    PASSERBY = "passerby"


class KnowledgeLevel(int, Enum):
    UNKNOWN = 0
    HEARD_OF = 1
    SEEN = 2
    ACQUAINTED = 3
    FAMILIAR = 4
    INTIMATE = 5


KNOWLEDGE_LABELS = {
    KnowledgeLevel.UNKNOWN: "❓ 未知",
    KnowledgeLevel.HEARD_OF: "📢 传闻中",
    KnowledgeLevel.SEEN: "👁️ 见过",
    KnowledgeLevel.ACQUAINTED: "🤝 相识",
    KnowledgeLevel.FAMILIAR: "💎 熟知",
    KnowledgeLevel.INTIMATE: "❤️ 知己",
}


@dataclass
class NpcEntry:
    npc_id: str
    name: str
    title: str = ""
    importance: NpcImportance = NpcImportance.PASSERBY
    faction: str = ""
    position_in_faction: str = ""
    power_level: str = ""
    reputation_level: int = 1
    age: int = 25
    gender: str = "男"
    personality: str = ""
    appearance: str = ""
    background: str = ""
    speaking_style: str = ""
    goals: str = ""
    long_term_goal: str = ""
    short_term_goals: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    location: str = ""
    alive: bool = True
    secrets: str = ""
    stats: Dict[str, Any] = field(default_factory=dict)
    relation_to_player: Dict[str, Any] = field(default_factory=lambda: {"favor": 50, "relation_type": "素未谋面"})
    first_met_day: int = 0
    last_met_day: int = 0
    times_met: int = 0
    interaction_history: List[Dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


@dataclass
class Rumor:
    rumor_id: str
    npc_id: str
    npc_name: str
    content: str
    day: int
    source: str = "传闻"
    discovered: bool = False
    is_major_event: bool = False


class NpcRegistry:
    def __init__(self, max_passersby: int = 10):
        self.world_npcs: Dict[str, NpcEntry] = {}
        self.local_npcs: Dict[str, NpcEntry] = {}
        self.passerby_npcs: "OrderedDict[str, NpcEntry]" = OrderedDict()
        self.max_passersby = max_passersby
        self.knowledge: Dict[str, KnowledgeLevel] = {}
        self.rumors: List[Rumor] = []
        self.info_visibility: str = "immersive"
        self.player_power_level: str = ""
        self.current_day: int = 1

    def set_info_visibility(self, mode: str):
        if mode in ("immersive", "semi", "god"):
            self.info_visibility = mode

    def get_info_visibility(self) -> str:
        return self.info_visibility

    def register_world_npc(self, npc_data: Dict[str, Any]) -> NpcEntry:
        npc_id = npc_data.get("npc_id") or f"npc_{len(self.world_npcs) + len(self.local_npcs)}"
        entry = NpcEntry(
            npc_id=npc_id,
            name=npc_data.get("name", "未知"),
            title=npc_data.get("title", ""),
            importance=NpcImportance.WORLD,
            faction=npc_data.get("faction", ""),
            position_in_faction=npc_data.get("position_in_faction", ""),
            power_level=npc_data.get("power_level", ""),
            reputation_level=npc_data.get("reputation_level", 5),
            age=npc_data.get("age", 25),
            gender=npc_data.get("gender", "男"),
            personality=npc_data.get("personality", ""),
            appearance=npc_data.get("appearance", ""),
            background=npc_data.get("background", ""),
            speaking_style=npc_data.get("speaking_style", ""),
            goals=npc_data.get("goals", ""),
            long_term_goal=npc_data.get("long_term_goal", ""),
            short_term_goals=npc_data.get("short_term_goals", []),
            tags=npc_data.get("tags", []),
            location=npc_data.get("initial_location", ""),
            alive=npc_data.get("alive", True),
            secrets=npc_data.get("secrets", ""),
            stats=npc_data.get("stats", {}),
            relation_to_player=npc_data.get("relation_to_player", {"favor": 50, "relation_type": "素未谋面"}),
        )
        self.world_npcs[npc_id] = entry
        self.knowledge[npc_id] = KnowledgeLevel.UNKNOWN
        return entry

    def register_local_npc(self, npc_data: Dict[str, Any], location: str = "") -> NpcEntry:
        npc_id = npc_data.get("npc_id") or f"local_{len(self.local_npcs) + int(time.time() * 1000) % 100000}"
        entry = NpcEntry(
            npc_id=npc_id,
            name=npc_data.get("name", "未知"),
            title=npc_data.get("title", ""),
            importance=NpcImportance.LOCAL,
            faction=npc_data.get("faction", ""),
            position_in_faction=npc_data.get("position_in_faction", ""),
            power_level=npc_data.get("power_level", ""),
            reputation_level=npc_data.get("reputation_level", 2),
            age=npc_data.get("age", 25),
            gender=npc_data.get("gender", "男"),
            personality=npc_data.get("personality", ""),
            appearance=npc_data.get("appearance", ""),
            background=npc_data.get("background", ""),
            speaking_style=npc_data.get("speaking_style", ""),
            goals=npc_data.get("goals", ""),
            long_term_goal=npc_data.get("long_term_goal", ""),
            short_term_goals=npc_data.get("short_term_goals", []),
            tags=npc_data.get("tags", []),
            location=location or npc_data.get("initial_location", ""),
            alive=True,
            stats=npc_data.get("stats", {}),
            relation_to_player=npc_data.get("relation_to_player", {"favor": 50, "relation_type": "陌生人"}),
        )
        self.local_npcs[npc_id] = entry
        self.knowledge[npc_id] = KnowledgeLevel.UNKNOWN
        return entry

    def register_passerby(self, npc_data: Dict[str, Any], location: str = "") -> NpcEntry:
        npc_id = npc_data.get("npc_id") or f"passerby_{int(time.time() * 1000)}_{len(self.passerby_npcs)}"
        entry = NpcEntry(
            npc_id=npc_id,
            name=npc_data.get("name", "路人"),
            title=npc_data.get("title", ""),
            importance=NpcImportance.PASSERBY,
            faction=npc_data.get("faction", ""),
            personality=npc_data.get("personality", "普通"),
            speaking_style=npc_data.get("speaking_style", ""),
            appearance=npc_data.get("appearance", ""),
            location=location,
            alive=True,
            tags=["路人"],
            reputation_level=0,
        )
        self.passerby_npcs[npc_id] = entry
        self.knowledge[npc_id] = KnowledgeLevel.SEEN
        while len(self.passerby_npcs) > self.max_passersby:
            old_id, _ = self.passerby_npcs.popitem(last=False)
            self.knowledge.pop(old_id, None)
        return entry

    def get_npc(self, npc_id: str) -> Optional[NpcEntry]:
        return self.world_npcs.get(npc_id) or self.local_npcs.get(npc_id) or self.passerby_npcs.get(npc_id)

    def get_all_npcs(self) -> List[NpcEntry]:
        return list(self.world_npcs.values()) + list(self.local_npcs.values()) + list(self.passerby_npcs.values())

    def get_world_npcs(self) -> List[NpcEntry]:
        return list(self.world_npcs.values())

    def get_npcs_by_faction(self, faction: str) -> List[NpcEntry]:
        return [n for n in self.world_npcs.values() if n.faction == faction]

    def get_npcs_by_location(self, location: str) -> List[NpcEntry]:
        result = []
        for npc in self.world_npcs.values():
            if npc.location == location:
                result.append(npc)
        for npc in self.local_npcs.values():
            if npc.location == location:
                result.append(npc)
        return result

    def mark_heard_of(self, npc_id: str, day: int = None):
        day = day or self.current_day
        current = self.knowledge.get(npc_id, KnowledgeLevel.UNKNOWN)
        if current < KnowledgeLevel.HEARD_OF:
            self.knowledge[npc_id] = KnowledgeLevel.HEARD_OF
            npc = self.get_npc(npc_id)
            if npc:
                logger.info("Player heard of NPC: %s (%s)", npc.name, KNOWLEDGE_LABELS[KnowledgeLevel.HEARD_OF])

    def mark_seen(self, npc_id: str, day: int = None):
        day = day or self.current_day
        current = self.knowledge.get(npc_id, KnowledgeLevel.UNKNOWN)
        if current < KnowledgeLevel.SEEN:
            self.knowledge[npc_id] = KnowledgeLevel.SEEN
        npc = self.get_npc(npc_id)
        if npc:
            if npc.first_met_day == 0:
                npc.first_met_day = day
            npc.last_met_day = day
            npc.times_met += 1

    def mark_acquainted(self, npc_id: str, day: int = None):
        day = day or self.current_day
        current = self.knowledge.get(npc_id, KnowledgeLevel.UNKNOWN)
        if current < KnowledgeLevel.ACQUAINTED:
            self.knowledge[npc_id] = KnowledgeLevel.ACQUAINTED
        npc = self.get_npc(npc_id)
        if npc:
            if npc.first_met_day == 0:
                npc.first_met_day = day
            npc.last_met_day = day
            npc.times_met += 1

    def mark_familiar(self, npc_id: str, day: int = None):
        day = day or self.current_day
        current = self.knowledge.get(npc_id, KnowledgeLevel.UNKNOWN)
        if current < KnowledgeLevel.FAMILIAR:
            self.knowledge[npc_id] = KnowledgeLevel.FAMILIAR

    def add_interaction(self, npc_id: str, interaction: Dict[str, Any], day: int = None):
        day = day or self.current_day
        npc = self.get_npc(npc_id)
        if not npc:
            return
        npc.interaction_history.append({"day": day, **interaction})
        npc.last_met_day = day
        if "favor_change" in interaction:
            npc.relation_to_player["favor"] = max(0, min(100, npc.relation_to_player.get("favor", 50) + interaction["favor_change"]))
        current = self.knowledge.get(npc_id, KnowledgeLevel.UNKNOWN)
        # times_met 统一由 mark_seen 维护，避免双重计数
        self.mark_seen(npc_id, day)
        if npc.times_met >= 3 and npc.importance != NpcImportance.WORLD:
            if self.knowledge.get(npc_id, KnowledgeLevel.UNKNOWN) < KnowledgeLevel.ACQUAINTED:
                self.knowledge[npc_id] = KnowledgeLevel.ACQUAINTED

    def add_rumor(self, npc_id: str, content: str, day: int = None, is_major_event: bool = False, source: str = "传闻") -> Rumor:
        day = day or self.current_day
        npc = self.get_npc(npc_id)
        npc_name = npc.name if npc else "某位大人物"
        import uuid
        rumor = Rumor(
            rumor_id=str(uuid.uuid4())[:8],
            npc_id=npc_id,
            npc_name=npc_name,
            content=content,
            day=day,
            source=source,
            discovered=False,
            is_major_event=is_major_event,
        )
        self.rumors.append(rumor)
        if npc and self.info_visibility == "immersive":
            rep = npc.reputation_level
            can_hear = False
            if rep <= 3:
                can_hear = True
            elif rep <= 6 and self._player_power_level_num() >= 2:
                can_hear = True
            elif rep <= 9 and self._player_power_level_num() >= 4:
                can_hear = True
            elif rep >= 10 and self._player_power_level_num() >= 6:
                can_hear = True
            if can_hear:
                self.mark_heard_of(npc_id, day)
        elif self.info_visibility in ("semi", "god"):
            if npc:
                self.knowledge[npc_id] = max(self.knowledge.get(npc_id, KnowledgeLevel.UNKNOWN), KnowledgeLevel.HEARD_OF)
        return rumor

    def get_pending_rumors(self, day: int = None, count: int = 2) -> List[Rumor]:
        day = day or self.current_day
        candidates = [r for r in self.rumors if not r.discovered and r.day <= day]
        candidates.sort(key=lambda r: (r.is_major_event, r.day), reverse=True)
        result = candidates[:count]
        for r in result:
            r.discovered = True
        return result

    def process_narrative(self, narrative: str, player_input: str = "", day: int = None) -> Dict[str, Any]:
        day = day or self.current_day
        new_heard = []
        new_seen = []
        new_rumors = []
        text = narrative + " " + player_input
        for npc_id, npc in list(self.world_npcs.items()) + list(self.local_npcs.items()):
            if npc.name and len(npc.name) >= 2 and npc.name in text:
                current_level = self.knowledge.get(npc_id, KnowledgeLevel.UNKNOWN)
                if current_level == KnowledgeLevel.UNKNOWN:
                    if any(kw in text for kw in ["听说", "传闻", "据说", "传言", "有人说", "谈起", "说起", "提到"]):
                        self.mark_heard_of(npc_id, day)
                        new_heard.append(npc.name)
                    elif any(kw in text for kw in ["看见", "见到", "遇到", "迎面", "走来", "站在", "坐在", "出现", "看着你", "对你", "说道", "开口"]):
                        self.mark_seen(npc_id, day)
                        new_seen.append(npc.name)
                elif current_level == KnowledgeLevel.HEARD_OF:
                    if any(kw in text for kw in ["看见", "见到", "遇到", "迎面", "走来", "站在", "坐在", "出现", "看着你", "对你", "说道", "开口", "交谈", "对话"]):
                        self.mark_seen(npc_id, day)
                        new_seen.append(npc.name)
            if npc.title and len(npc.title) >= 2 and npc.title in text:
                current_level = self.knowledge.get(npc_id, KnowledgeLevel.UNKNOWN)
                if current_level == KnowledgeLevel.UNKNOWN:
                    self.mark_heard_of(npc_id, day)
                    if npc.name not in new_heard:
                        new_heard.append(npc.title)
        import random
        if random.random() < 0.15 and self.world_npcs:
            candidates = [n for n in self.world_npcs.values() if n.importance == NpcImportance.WORLD and self.knowledge.get(n.npc_id, KnowledgeLevel.UNKNOWN) <= KnowledgeLevel.HEARD_OF]
            if candidates:
                npc = random.choice(candidates)
                rumor_templates = [
                    f"江湖传言，{npc.faction}的{npc.title}{npc.name}最近有大动作...",
                    f"你偶尔听人说起{npc.name}的一些事迹，似乎是个了不得的人物。",
                    f"茶馆里有人在议论{npc.name}，说他{random.choice(['武功深不可测', '手段狠辣', '为人正直', '神秘莫测', '背景深厚'])}。",
                    f"最近{npc.faction}那边风声很紧，据说和{npc.name}有关。",
                ]
                rumor_text = random.choice(rumor_templates)
                if random.random() < 0.5 and self.knowledge.get(npc.npc_id, KnowledgeLevel.UNKNOWN) == KnowledgeLevel.UNKNOWN:
                    # 玩家不认识该NPC时，用模糊称呼替换名字
                    rumor_name = "那人" if npc.gender == "male" else "那女子"
                    rumor_text = rumor_text.replace(npc.name, rumor_name)
                rumor = self.add_rumor(npc.npc_id, rumor_text, day=day, source="街头巷尾")
                new_rumors.append(rumor_text)
        return {"new_heard": new_heard, "new_seen": new_seen, "new_rumors": new_rumors}

    def _player_power_level_num(self) -> int:
        """根据玩家声望/等级返回数值估算"""
        if not self.player_power_level:
            return 1
        # 常见修为/武力等级映射
        level_map = {
            "凡人": 1, "入门": 1, "初学": 1,
            "炼气": 2, "练气": 2,
            "筑基": 3, "内力": 3,
            "金丹": 4, "结丹": 4,
            "元婴": 5,
            "化神": 6,
            "宗师": 6, "大宗师": 7,
            "渡劫": 7, "大乘": 8,
            "仙人": 9, "仙": 9,
        }
        for key, val in level_map.items():
            if key in self.player_power_level:
                return val
        # 尝试解析数字
        try:
            return int(self.player_power_level)
        except (ValueError, TypeError):
            return 1

    def get_npc_visible_info(self, npc_id: str) -> Dict[str, Any]:
        npc = self.get_npc(npc_id)
        if not npc:
            return {"exists": False}
        if self.info_visibility == "god":
            return self._npc_to_dict(npc, KnowledgeLevel.INTIMATE)
        level = self.knowledge.get(npc_id, KnowledgeLevel.UNKNOWN)
        return self._npc_to_dict(npc, level)

    def _npc_to_dict(self, npc: NpcEntry, level: KnowledgeLevel) -> Dict[str, Any]:
        base = {
            "npc_id": npc.npc_id,
            "importance": npc.importance.value,
            "alive": npc.alive,
        }
        if level >= KnowledgeLevel.HEARD_OF:
            base.update({
                "name": npc.name if level >= KnowledgeLevel.ACQUAINTED or self.info_visibility == "semi" else f"「{npc.title}」" if npc.title else "？？？",
                "title": npc.title,
                "faction": npc.faction,
                "power_level": npc.power_level if level >= KnowledgeLevel.ACQUAINTED else "？？？",
                "knowledge_level": level,
                "knowledge_label": KNOWLEDGE_LABELS.get(level, "未知"),
                "reputation_level": npc.reputation_level,
            })
        else:
            base.update({
                "name": "？？？",
                "title": "？？？",
                "faction": "未知势力",
                "power_level": "？？？",
                "knowledge_level": level,
                "knowledge_label": KNOWLEDGE_LABELS.get(level, "未知"),
            })
        if level >= KnowledgeLevel.SEEN:
            base["appearance"] = npc.appearance
            base["gender"] = npc.gender
            base["age_range"] = f"{npc.age - 5}~{npc.age + 5}岁" if npc.age else "未知"
        if level >= KnowledgeLevel.ACQUAINTED:
            base["age"] = npc.age
            base["personality"] = npc.personality
            base["position_in_faction"] = npc.position_in_faction
            base["relation_to_player"] = npc.relation_to_player
            base["times_met"] = npc.times_met
        if level >= KnowledgeLevel.FAMILIAR:
            base["background"] = npc.background
            base["goals"] = npc.goals
            base["long_term_goal"] = npc.long_term_goal
            base["speaking_style"] = npc.speaking_style
            base["stats"] = npc.stats
        if level >= KnowledgeLevel.INTIMATE:
            base["secrets"] = npc.secrets
            base["short_term_goals"] = npc.short_term_goals
            base["interaction_history"] = npc.interaction_history[-20:]
        return base

    def get_world_npc_directory(self) -> Dict[str, Any]:
        factions = {}
        unknown_count = 0
        for npc in self.world_npcs.values():
            if not npc.alive:
                continue
            level = self.knowledge.get(npc.npc_id, KnowledgeLevel.UNKNOWN)
            if level == KnowledgeLevel.UNKNOWN and self.info_visibility == "immersive":
                unknown_count += 1
                continue
            fac = npc.faction or "其他"
            if fac not in factions:
                factions[fac] = []
            info = self.get_npc_visible_info(npc.npc_id)
            factions[fac].append(info)
        result = {
            "factions": factions,
            "total_world_npcs": len([n for n in self.world_npcs.values() if n.alive]),
            "known_count": sum(len(v) for v in factions.values()),
            "unknown_count": unknown_count,
            "local_npcs_count": len(self.local_npcs),
            "recent_passersby_count": len(self.passerby_npcs),
        }
        return result

    def to_dict(self) -> Dict[str, Any]:
        return {
            "world_npcs": {nid: self._entry_to_dict(n) for nid, n in self.world_npcs.items()},
            "local_npcs": {nid: self._entry_to_dict(n) for nid, n in self.local_npcs.items()},
            "passerby_npcs": {nid: self._entry_to_dict(n) for nid, n in self.passerby_npcs.items()},
            "knowledge": {nid: int(level) for nid, level in self.knowledge.items()},
            "rumors": [self._rumor_to_dict(r) for r in self.rumors],
            "info_visibility": self.info_visibility,
            "current_day": self.current_day,
        }

    def _entry_to_dict(self, npc: NpcEntry) -> Dict[str, Any]:
        return {
            "npc_id": npc.npc_id,
            "name": npc.name,
            "title": npc.title,
            "importance": npc.importance.value,
            "faction": npc.faction,
            "position_in_faction": npc.position_in_faction,
            "power_level": npc.power_level,
            "reputation_level": npc.reputation_level,
            "age": npc.age,
            "gender": npc.gender,
            "personality": npc.personality,
            "appearance": npc.appearance,
            "background": npc.background,
            "speaking_style": npc.speaking_style,
            "goals": npc.goals,
            "long_term_goal": npc.long_term_goal,
            "short_term_goals": npc.short_term_goals,
            "tags": npc.tags,
            "location": npc.location,
            "alive": npc.alive,
            "secrets": npc.secrets,
            "stats": npc.stats,
            "relation_to_player": npc.relation_to_player,
            "first_met_day": npc.first_met_day,
            "last_met_day": npc.last_met_day,
            "times_met": npc.times_met,
            "interaction_history": npc.interaction_history[-50:],
        }

    def _rumor_to_dict(self, r: Rumor) -> Dict[str, Any]:
        return {
            "rumor_id": r.rumor_id,
            "npc_id": r.npc_id,
            "npc_name": r.npc_name,
            "content": r.content,
            "day": r.day,
            "source": r.source,
            "discovered": r.discovered,
            "is_major_event": r.is_major_event,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NpcRegistry":
        reg = cls()
        reg.info_visibility = data.get("info_visibility", "immersive")
        reg.current_day = data.get("current_day", 1)
        for nid, ndata in data.get("world_npcs", {}).items():
            ndata["npc_id"] = nid
            reg.world_npcs[nid] = NpcEntry(
                npc_id=nid,
                name=ndata.get("name", "未知"),
                title=ndata.get("title", ""),
                importance=NpcImportance.WORLD,
                faction=ndata.get("faction", ""),
                position_in_faction=ndata.get("position_in_faction", ""),
                power_level=ndata.get("power_level", ""),
                reputation_level=ndata.get("reputation_level", 5),
                age=ndata.get("age", 25),
                gender=ndata.get("gender", "男"),
                personality=ndata.get("personality", ""),
                appearance=ndata.get("appearance", ""),
                background=ndata.get("background", ""),
                speaking_style=ndata.get("speaking_style", ""),
                goals=ndata.get("goals", ""),
                long_term_goal=ndata.get("long_term_goal", ""),
                short_term_goals=ndata.get("short_term_goals", []),
                tags=ndata.get("tags", []),
                location=ndata.get("location", ""),
                alive=ndata.get("alive", True),
                secrets=ndata.get("secrets", ""),
                stats=ndata.get("stats", {}),
                relation_to_player=ndata.get("relation_to_player", {"favor": 50, "relation_type": "素未谋面"}),
                first_met_day=ndata.get("first_met_day", 0),
                last_met_day=ndata.get("last_met_day", 0),
                times_met=ndata.get("times_met", 0),
                interaction_history=ndata.get("interaction_history", []),
            )
        for nid, ndata in data.get("local_npcs", {}).items():
            ndata["npc_id"] = nid
            reg.local_npcs[nid] = NpcEntry(
                npc_id=nid,
                name=ndata.get("name", "未知"),
                title=ndata.get("title", ""),
                importance=NpcImportance.LOCAL,
                faction=ndata.get("faction", ""),
                reputation_level=ndata.get("reputation_level", 2),
                age=ndata.get("age", 25),
                gender=ndata.get("gender", "男"),
                personality=ndata.get("personality", ""),
                appearance=ndata.get("appearance", ""),
                location=ndata.get("location", ""),
                alive=ndata.get("alive", True),
                tags=ndata.get("tags", []),
                relation_to_player=ndata.get("relation_to_player", {"favor": 50, "relation_type": "陌生人"}),
                first_met_day=ndata.get("first_met_day", 0),
                last_met_day=ndata.get("last_met_day", 0),
                times_met=ndata.get("times_met", 0),
            )
        for nid, ndata in data.get("passerby_npcs", {}).items():
            ndata["npc_id"] = nid
            entry = NpcEntry(
                npc_id=nid,
                name=ndata.get("name", "路人"),
                title=ndata.get("title", ""),
                importance=NpcImportance.PASSERBY,
                faction=ndata.get("faction", ""),
                position_in_faction=ndata.get("position_in_faction", ""),
                power_level=ndata.get("power_level", ""),
                reputation_level=ndata.get("reputation_level", 0),
                age=ndata.get("age", 25),
                gender=ndata.get("gender", "男"),
                personality=ndata.get("personality", "普通"),
                appearance=ndata.get("appearance", ""),
                background=ndata.get("background", ""),
                speaking_style=ndata.get("speaking_style", ""),
                goals=ndata.get("goals", ""),
                long_term_goal=ndata.get("long_term_goal", ""),
                short_term_goals=ndata.get("short_term_goals", []),
                tags=ndata.get("tags", ["路人"]),
                location=ndata.get("location", ""),
                alive=ndata.get("alive", True),
                secrets=ndata.get("secrets", ""),
                stats=ndata.get("stats", {}),
                relation_to_player=ndata.get("relation_to_player", {"favor": 50, "relation_type": "素未谋面"}),
                first_met_day=ndata.get("first_met_day", 0),
                last_met_day=ndata.get("last_met_day", 0),
                times_met=ndata.get("times_met", 0),
                interaction_history=ndata.get("interaction_history", []),
            )
            reg.passerby_npcs[nid] = entry
        for nid, level in data.get("knowledge", {}).items():
            reg.knowledge[nid] = KnowledgeLevel(level)
        for rdata in data.get("rumors", []):
            reg.rumors.append(Rumor(
                rumor_id=rdata.get("rumor_id", ""),
                npc_id=rdata.get("npc_id", ""),
                npc_name=rdata.get("npc_name", ""),
                content=rdata.get("content", ""),
                day=rdata.get("day", 1),
                source=rdata.get("source", "传闻"),
                discovered=rdata.get("discovered", False),
                is_major_event=rdata.get("is_major_event", False),
            ))
        return reg
