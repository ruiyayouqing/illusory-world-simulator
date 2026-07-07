from __future__ import annotations
import random
from .schemas import NPCState, WorldState, PlayerState


PERCEPTION_ZONES = {
    "active": {
        "max_distance": 2,
        "description": "同场景/相邻场景，完全激活",
        "simulation_rate": 1.0,
        "can_interact": True,
        "full_dialogue": True,
    },
    "aware": {
        "max_distance": 5,
        "description": "同区域，知道大致动向",
        "simulation_rate": 0.6,
        "can_interact": False,
        "full_dialogue": False,
    },
    "rumor": {
        "max_distance": 15,
        "description": "远处，只有传闻",
        "simulation_rate": 0.3,
        "can_interact": False,
        "full_dialogue": False,
    },
    "sleeping": {
        "max_distance": 999,
        "description": "遥远，概率性微更新",
        "simulation_rate": 0.05,
        "can_interact": False,
        "full_dialogue": False,
    },
}


class NPCPerceptionSystem:
    def __init__(self):
        self.npc_zones: dict[str, str] = {}
        self.npc_last_active_day: dict[str, int] = {}
        self.npc_sleep_events: list[dict] = []

    def classify_npc_zone(self, npc: NPCState, player: PlayerState,
                          world_state: WorldState) -> str:
        distance = self._estimate_distance(npc, player, world_state)

        if distance <= PERCEPTION_ZONES["active"]["max_distance"]:
            zone = "active"
        elif distance <= PERCEPTION_ZONES["aware"]["max_distance"]:
            zone = "aware"
        elif distance <= PERCEPTION_ZONES["rumor"]["max_distance"]:
            zone = "rumor"
        else:
            zone = "sleeping"

        old_zone = self.npc_zones.get(npc.agent_id, "sleeping")
        self.npc_zones[npc.agent_id] = zone

        if old_zone != zone:
            self.npc_last_active_day[npc.agent_id] = world_state.current_day

        return zone

    def _estimate_distance(self, npc: NPCState, player: PlayerState,
                           world_state: WorldState) -> int:
        if npc.current_location == player.location:
            return 0

        same_area = self._same_area(npc.current_location, player.location)
        if same_area:
            return 1

        return 10

    def _same_area(self, loc_a: str, loc_b: str) -> bool:
        if not loc_a or not loc_b:
            return False
        a = loc_a.lower().replace("_", "").replace(" ", "")
        b = loc_b.lower().replace("_", "").replace(" ", "")
        if a == b:
            return True
        a_parts = set(a.split("/"))
        b_parts = set(b.split("/"))
        if a_parts & b_parts:
            return True
        return False

    def get_zone_info(self, npc_id: str) -> dict:
        zone = self.npc_zones.get(npc_id, "sleeping")
        config = PERCEPTION_ZONES[zone]
        return {
            "zone": zone,
            "description": config["description"],
            "simulation_rate": config["simulation_rate"],
            "can_interact": config["can_interact"],
            "full_dialogue": config["full_dialogue"],
            "last_active_day": self.npc_last_active_day.get(npc_id, 0),
        }

    def should_simulate(self, npc_id: str, world_state: WorldState) -> bool:
        zone = self.npc_zones.get(npc_id, "sleeping")
        config = PERCEPTION_ZONES[zone]
        return random.random() < config["simulation_rate"]

    def should_full_simulate(self, npc_id: str) -> bool:
        zone = self.npc_zones.get(npc_id, "sleeping")
        return zone == "active"

    def get_sleeping_npcs(self) -> list[str]:
        return [nid for nid, z in self.npc_zones.items() if z == "sleeping"]

    def get_active_npcs(self) -> list[str]:
        return [nid for nid, z in self.npc_zones.items() if z == "active"]

    def batch_classify(self, npcs: list[NPCState], player: PlayerState,
                       world_state: WorldState) -> dict[str, str]:
        zones = {}
        for npc in npcs:
            zone = self.classify_npc_zone(npc, player, world_state)
            zones[npc.agent_id] = zone
        return zones

    def simulate_sleeping_npc(self, npc: NPCState, world_state: WorldState) -> dict | None:
        if not self.should_simulate(npc.agent_id, world_state):
            return None

        events = []
        roll = random.random()

        if npc.age >= 20 and roll < 0.02:
            events.append({"type": "marriage", "detail": f"{npc.name}成亲了"})
        if roll < 0.05:
            events.append({"type": "travel", "detail": f"{npc.name}出远门了"})
        if npc.stats.health < 50 and roll < 0.08:
            events.append({"type": "illness", "detail": f"{npc.name}生病了"})
        if roll < 0.03:
            events.append({"type": "trade", "detail": f"{npc.name}做了笔生意"})

        if events:
            event = random.choice(events)
            self.npc_sleep_events.append({
                "npc_id": npc.agent_id,
                "npc_name": npc.name,
                "day": world_state.current_day,
                "event": event,
            })
            return event
        return None

    def get_sleep_events_summary(self, day: int) -> list[dict]:
        return [e for e in self.npc_sleep_events if e["day"] == day]

    def get_zone_display(self) -> list[dict]:
        result = []
        for npc_id, zone in self.npc_zones.items():
            config = PERCEPTION_ZONES[zone]
            result.append({
                "npc_id": npc_id,
                "zone": zone,
                "zone_cn": {"active": "活跃", "aware": "感知", "rumor": "传闻", "sleeping": "沉睡"}.get(zone, zone),
                "description": config["description"],
            })
        return result

    def to_dict(self) -> dict:
        # [Bug] 必须序列化全部三个字段，否则读档后 npc_last_active_day 和 npc_sleep_events 丢失
        return {
            "npc_zones": dict(self.npc_zones),
            "npc_last_active_day": dict(self.npc_last_active_day),
            "npc_sleep_events": list(self.npc_sleep_events),
        }

    def from_dict(self, data: dict):
        # [Bug] 兼容旧存档格式（旧格式直接是 npc_zones dict，新格式是含三个字段的 dict）
        if data and "npc_zones" in data:
            self.npc_zones = dict(data.get("npc_zones", {}))
            self.npc_last_active_day = dict(data.get("npc_last_active_day", {}))
            self.npc_sleep_events = list(data.get("npc_sleep_events", []))
        elif data:
            # 旧格式：data 本身就是 npc_zones
            self.npc_zones = dict(data)
            self.npc_last_active_day = {}
            self.npc_sleep_events = []
        else:
            self.npc_zones = {}
            self.npc_last_active_day = {}
            self.npc_sleep_events = []
