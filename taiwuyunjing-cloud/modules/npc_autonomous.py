from __future__ import annotations
import random
from .schemas import NPCState, WorldState, PlayerState
from .llm.base_llm import BaseLLM
from .prompt_utils import resolve_location_name  # [Bug] location code → display name


class NpcAutonomous:
    def __init__(self, llm: BaseLLM):
        self.llm = llm
        self.npc_logs: list[dict] = []

    def npc_daily_routine(self, npc: NPCState, world_state: WorldState,
                          player: PlayerState = None) -> dict:
        is_near_player = (player and npc.current_location == player.location) if player else False

        prompt = f"""作为NPC，根据你的性格和当前情况，决定今天的行动。

【NPC信息】
姓名: {npc.name}，{npc.age}岁
性格: {npc.personality}
标签: {', '.join(npc.tags)}
位置: {resolve_location_name(npc.current_location, world_state)}  # [Bug] location code → display name
目标: {npc.ai_behavior.get('current_goal', '')}

【世界状态】
第{world_state.current_day}天 {world_state.current_time}
天气: {world_state.weather}
季节: {world_state.season}

{"【玩家在附近】" if is_near_player else ""}

【输出JSON格式】
{{
    "action": "work/rest/travel/social/explore/idle",
    "detail": "50字行动描述",
    "new_location": "如果移动，新地点",
    "mood_change": -2到2,
    "player_interaction": "如果与玩家有交互，描述交互内容（可选）"
}}"""
        response = self.llm.chat_json(prompt, temperature=0.7)

        action = response.get("action", "idle")
        # [Bug] 验证 new_location 是否为世界中存在的地点，防止 LLM 幻觉导致 NPC 移动到无效位置
        # LLM 返回的是显示名，需要反向查找为 location code
        new_loc = response.get("new_location")
        if new_loc and new_loc != npc.current_location:
            resolved_code = self._resolve_location_to_code(new_loc, world_state)
            if resolved_code:
                npc.current_location = resolved_code

        npc.recent_actions.append({
            "day": world_state.current_day,
            "action": action,
            "detail": response.get("detail", ""),
            "location": npc.current_location,
        })
        if len(npc.recent_actions) > 10:
            npc.recent_actions = npc.recent_actions[-10:]

        log = {
            "npc_id": npc.agent_id,
            "npc_name": npc.name,
            "day": world_state.current_day,
            "time": world_state.current_time,
            "action": action,
            "detail": response.get("detail", ""),
            "location": npc.current_location,
        }
        self.npc_logs.append(log)
        self.npc_logs = self.npc_logs[-500:]

        return {
            "npc_id": npc.agent_id,
            "npc_name": npc.name,
            "action": action,
            "detail": response.get("detail", ""),
            "player_interaction": response.get("player_interaction"),
        }

    def batch_npc_actions(self, npcs: list[NPCState], world_state: WorldState,
                          player: PlayerState = None) -> list[dict]:
        results = []
        current_day = world_state.current_day
        for npc in npcs:
            # [Bug] 每日行动限制：每个NPC每天最多行动1次，防止一天内多次搬家/做事
            if npc.last_action_day == current_day:
                continue
            # [Bug] 降低行动概率：60%→25%，减少NPC每天行动频率
            if random.random() < 0.25:
                result = self.npc_daily_routine(npc, world_state, player)
                npc.last_action_day = current_day
                results.append(result)
        return results

    def get_npc_nearby_actions(self, player_location: str, day: int) -> list[dict]:
        return [log for log in self.npc_logs
                if log["location"] == player_location and log["day"] == day]

    def get_npc_logs_today(self, day: int) -> list[dict]:
        return [log for log in self.npc_logs if log["day"] == day]

    def get_npc_summary(self, npc_id: str) -> str:
        logs = [l for l in self.npc_logs if l["npc_id"] == npc_id][-5:]
        if not logs:
            return "没有关于这个人的记录。"
        lines = [f"【{logs[0]['npc_name']}近况】"]
        for log in logs:
            lines.append(f"  第{log['day']}天 {log['time']}: {log['detail'][:50]}")
        return "\n".join(lines)

    @staticmethod
    def _resolve_location_to_code(loc_display: str, world_state) -> str | None:
        """[Bug] 反向查找：将 LLM 返回的显示名转为 location code。
        先精确匹配显示名，再模糊匹配。"""
        if not loc_display or not world_state:
            return None
        locations = getattr(world_state, 'locations', None) or {}
        if not locations:
            return None
        # 精确匹配显示名
        for code, loc_data in locations.items():
            if isinstance(loc_data, dict):
                name = loc_data.get("location_name", loc_data.get("name", ""))
            elif hasattr(loc_data, 'location_name'):
                name = loc_data.location_name or ""
            elif hasattr(loc_data, 'name'):
                name = loc_data.name or ""
            else:
                name = str(loc_data)
            if name and name == loc_display:
                return code
        # 模糊匹配：显示名包含 loc_display 或反过来
        for code, loc_data in locations.items():
            if isinstance(loc_data, dict):
                name = loc_data.get("location_name", loc_data.get("name", ""))
            elif hasattr(loc_data, 'location_name'):
                name = loc_data.location_name or ""
            elif hasattr(loc_data, 'name'):
                name = loc_data.name or ""
            else:
                name = str(loc_data)
            if name and (loc_display in name or name in loc_display):
                return code
        # 最后尝试直接当 code 用
        if loc_display in locations:
            return loc_display
        return None
