from __future__ import annotations
import json
from .schemas import WorldState, MacroEvent, LocationDef, PlayerState, NPCState
from .llm.base_llm import BaseLLM
from .db.sqlite_db import WorldDB
from .prompt.world_prompts import (
    EVENT_GENERATION_PROMPT, EVENT_PROPAGATION_PROMPT,
    SCENE_ACTORS_PROMPT, ENVIRONMENT_RESPONSE_PROMPT,
    DAILY_WORLD_SUMMARY_PROMPT,
)


class WorldAgent:
    def __init__(self, llm: BaseLLM, db: WorldDB, world_def: dict):
        self.llm = llm
        self.db = db
        self.world_def = world_def
        self.locations: dict[str, LocationDef] = {}
        self.map_data: dict = {}
        self._load_world()

    def _load_world(self):
        locs = self.world_def.get("locations", {})
        for code, data in locs.items():
            self.locations[code] = LocationDef(
                location_code=code,
                location_name=data.get("location_name", code),
                description=data.get("description", ""),
                detail=data.get("detail", ""),
                special_actions=data.get("special_actions", []),
            )
        self.map_data = self.world_def.get("map", {})

    def get_location(self, code: str) -> LocationDef | None:
        return self.locations.get(code)

    def get_distance(self, loc_a: str, loc_b: str) -> int | None:
        if loc_a == loc_b:
            return 0
        distances = self.map_data.get(loc_a, {})
        return distances.get(loc_b)

    def get_nearby_locations(self, code: str, max_dist: int = 10) -> list[dict]:
        result = []
        distances = self.map_data.get(code, {})
        for loc, dist in distances.items():
            if dist <= max_dist:
                result.append({"location": loc, "distance": dist})
        return sorted(result, key=lambda x: x["distance"])

    def generate_event(self, world_state: WorldState, day: int, seq: int = 1) -> dict:
        event_prompt = EVENT_GENERATION_PROMPT.format(
            world_state=self._format_world_state(world_state),
            event_history=world_state.event_history_summary or "无",
        )
        response = self.llm.chat_json(event_prompt, temperature=0.8)
        if "event_id" not in response:
            response["event_id"] = f"evt_{day}_{seq}"
        if "day" not in response:
            response["day"] = day
        event = MacroEvent(
            event_id=response.get("event_id", f"evt_{day}_{seq}"),
            event_type=response.get("event_type", "social"),
            description=response.get("description", ""),
            affected_locations=response.get("affected_locations", []),
            impact_level=response.get("impact_level", 5),
            start_day=day,
        )
        return {
            "event": event,
            "raw": response,
        }

    def propagate_event(self, event: MacroEvent, player: PlayerState,
                        current_time: str = "上午") -> str:
        location_def = self.locations.get(player.location)
        loc_name = location_def.location_name if location_def else player.location
        loc_desc = location_def.description if location_def else ""

        prompt = EVENT_PROPAGATION_PROMPT.format(
            macro_event=event.description,
            player_location=f"{loc_name}: {loc_desc}",
            player_effects=", ".join(player.status_effects) if player.status_effects else "正常",
            current_time=current_time,
        )
        return self.llm.chat(prompt, temperature=0.8)

    def decide_scene_actors(self, location: str, available_npcs: list[dict],
                            current_event: str = "") -> dict:
        npc_list_text = "\n".join([
            f"- {npc['agent_id']}: {npc['name']} ({npc.get('personality', '')}) "
            f"位置: {npc.get('current_location', '未知')}"
            for npc in available_npcs
        ])
        location_def = self.locations.get(location)
        loc_text = f"{location_def.location_name}: {location_def.description}" if location_def else location

        prompt = SCENE_ACTORS_PROMPT.format(
            location_name=loc_text.split(":")[0],
            location_description=loc_text,
            npc_list=npc_list_text or "无可用NPC",
            current_event=current_event or "无特殊事件",
        )
        response = self.llm.chat_json(prompt, temperature=0.5)
        return response

    def environment_response(self, player_action: str, player: PlayerState,
                             time: str = "上午", weather: str = "晴朗") -> str:
        location_def = self.locations.get(player.location)
        loc_name = location_def.location_name if location_def else player.location
        objects = ", ".join(location_def.special_actions) if location_def else "无"

        prompt = ENVIRONMENT_RESPONSE_PROMPT.format(
            player_action=player_action,
            location=loc_name,
            time=time,
            weather=weather,
            objects=objects,
            strength=player.stats.strength,
            agility=player.stats.agility,
            intelligence=player.stats.intelligence,
        )
        return self.llm.chat(prompt, temperature=0.7)

    def generate_daily_summary(self, world_state: WorldState,
                               today_events: list[dict],
                               player_actions: list[str]) -> str:
        events_text = "\n".join([
            f"- [{e.get('event_type', '')}] {e.get('description', '')}"
            for e in today_events
        ]) or "无事件"
        actions_text = "\n".join([f"- {a}" for a in player_actions]) or "无行动"

        prompt = DAILY_WORLD_SUMMARY_PROMPT.format(
            today_events=events_text,
            player_actions=actions_text,
            world_state=self._format_world_state(world_state),
        )
        return self.llm.chat(prompt, temperature=0.8)

    def update_world_state(self, world_state: WorldState, event: MacroEvent):
        world_state.active_events.append(event)
        world_state.crisis_level = min(10, world_state.crisis_level + max(0, event.impact_level - 5))

        event_summary = f"第{event.start_day}天: {event.description[:50]}"
        if world_state.event_history_summary:
            world_state.event_history_summary += f"\n{event_summary}"
        else:
            world_state.event_history_summary = event_summary

        self.db.log_event({
            "day": event.start_day,
            "event_id": event.event_id,
            "event_type": event.event_type,
            "description": event.description,
            "affected_locations": event.affected_locations,
            "impact_level": event.impact_level,
        })

    def advance_time(self, world_state: WorldState, time_slot: str = None):
        time_slots = ["清晨", "上午", "中午", "下午", "傍晚", "深夜"]
        if time_slot and time_slot in time_slots:
            world_state.current_time = time_slot
            idx = time_slots.index(time_slot)
            if idx == 0 and world_state.current_time != "清晨":
                world_state.current_day += 1
        else:
            current_idx = time_slots.index(world_state.current_time) if world_state.current_time in time_slots else 0
            next_idx = (current_idx + 1) % len(time_slots)
            if next_idx == 0:
                world_state.current_day += 1
            world_state.current_time = time_slots[next_idx]

        world_state.season = self._get_season(world_state.current_day)

    def _get_season(self, day: int) -> str:
        day_in_year = day % 120
        if day_in_year < 30:
            return "春季"
        elif day_in_year < 60:
            return "夏季"
        elif day_in_year < 90:
            return "秋季"
        else:
            return "冬季"

    def _format_world_state(self, ws: WorldState) -> str:
        lines = [
            f"世界: {ws.world_name} ({ws.world_type})",
            f"日期: 第{ws.current_day}天 {ws.current_time}",
            f"季节: {ws.season}",
            f"天气: {ws.weather}",
            f"危机等级: {ws.crisis_level}/10",
        ]
        if ws.factions:
            factions = [f"{k}(实力{v.power})" for k, v in ws.factions.items()]
            lines.append(f"势力: {', '.join(factions)}")
        if ws.active_events:
            lines.append(f"活跃事件: {len(ws.active_events)}个")
        return "\n".join(lines)
