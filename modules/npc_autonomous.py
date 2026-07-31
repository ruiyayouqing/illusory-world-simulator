from __future__ import annotations
import logging
import random
from .schemas import NPCState, WorldState, PlayerState
from .llm.base_llm import BaseLLM
from .prompt_utils import resolve_location_name  # [Bug] location code → display name

logger = logging.getLogger("chronoverse.npc_autonomous")


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
        current_time = getattr(world_state, "current_time", "")
        # [v1.2] 时序轮询：与 NPCAgent.batch_evolve 保持一致
        turn_id = f"{current_day}:{current_time}"
        for npc in npcs:
            # [NovelRoleplay] 跳过休眠 NPC（未来角色未登场前不参与自主行动）
            if getattr(npc, 'is_dormant', False):
                continue
            # [v1.2] 时序轮询：按时段限制（取代原 last_action_day 每日限制）
            if npc.last_action_turn == turn_id:
                continue
            # [Bug] 降低行动概率：60%→25%，减少NPC每天行动频率
            if random.random() < 0.25:
                result = self.npc_daily_routine(npc, world_state, player)
                npc.last_action_turn = turn_id
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

    # ── [NovelRoleplay] 轻量幕后演化（规则驱动，不调 LLM） ──────

    def offline_evolve(self, npc: NPCState, world_state: WorldState,
                        days_passed: int = 1) -> dict:
        """[NovelRoleplay] 潜在 NPC 的轻量幕后演化。
        规则驱动为主，目标判定时调用 GoalEvaluator（含 LLM 判定）。

        用于 dormant NPC 被激活后，或活跃 NPC 的低优先级日子（不调 LLM 的日子）。
        每次 days_passed 天推进一次，模拟 NPC 在幕后的发展。

        [v1.2 改造] 不再机械 pop 队首短期目标，改为：
          1. 用 GoalEvaluator 判定当前短期目标是否达成（规则触发 + LLM 派生）
          2. 若 NPC 无当前目标且有强动机，用 MotivationEngine→Goal 生成
          3. 位置演化、状态演化保持规则驱动

        返回：{"npc_id", "npc_name", "days_evolved", "changes": [str]}
        """
        changes: list[str] = []
        current_day = world_state.current_day

        # 1. 目标演化：调用 GoalEvaluator 判定短期目标
        ai_behavior = npc.ai_behavior or {}
        current_goal = (ai_behavior.get("current_goal") or "").strip()
        try:
            from .goal_evaluator import get_goal_evaluator
            evaluator = get_goal_evaluator()
            # all_npcs 通过 world_state 暂存获取（game_engine 注入）
            all_npcs = getattr(world_state, "_all_npcs_ref", {}) or {}
            if current_goal and all_npcs:
                result = evaluator.evaluate_short_term_goal(
                    npc, world_state, all_npcs,
                )
                if result.get("achieved"):
                    new_goal = result.get("next_short_term_goal", "")
                    summary = result.get("achievement_summary", "")
                    changes.append(f"完成短期目标「{current_goal}」({summary})→ 派生「{new_goal}」")
            # 若无当前目标，尝试从动机派生
            if not (npc.ai_behavior or {}).get("current_goal"):
                new_goal = self._try_generate_goal_from_motivation(npc, world_state, all_npcs)
                if new_goal:
                    ai_behavior = npc.ai_behavior or {}
                    ai_behavior["current_goal"] = new_goal
                    ai_behavior.setdefault("short_term_goals", []).insert(0, new_goal)
                    npc.ai_behavior = ai_behavior
                    changes.append(f"由动机派生新短期目标：{new_goal}")
        except Exception as e:
            logger.warning("Goal evaluation failed for %s: %s", npc.name, e)

        # 2. 位置演化（规则：根据 goal 关键词决定是否移动）
        ai_behavior = npc.ai_behavior or {}
        long_term = ai_behavior.get("long_term_goal", "")
        current_goal = ai_behavior.get("current_goal", "")
        target_loc = self._infer_target_location_from_goal(current_goal, world_state)
        if target_loc and target_loc != npc.current_location:
            # 30% 概率移动（不是每天都移动）
            if random.random() < 0.3:
                old_loc = npc.current_location
                npc.current_location = target_loc
                npc.recent_actions.append({
                    "day": current_day,
                    "action": "travel",
                    "detail": f"（幕后）从 {old_loc} 前往 {target_loc}",
                })
                changes.append(f"从 {old_loc} 前往 {target_loc}")

        # 3. 状态演化（规则：health/energy 微调，年龄增长）
        if hasattr(npc.stats, 'energy'):
            # 幕后生活平稳，体力恢复
            npc.stats.energy = min(100, npc.stats.energy + 5 * days_passed)
        if days_passed > 0 and hasattr(npc, 'age'):
            # 每 30 天长一岁（简化）
            new_age = npc.age + days_passed // 30
            if new_age != npc.age:
                npc.age = new_age
                changes.append(f"年龄增长至 {new_age}")

        # 4. 关系演化（规则：与原著既定关系对齐）
        # 如果 NPC 有 original_future，检查是否该激活某些关系
        # 这里简化：不主动改关系，留给 GraphRAG 的 is_future 机制处理

        return {
            "npc_id": npc.agent_id,
            "npc_name": npc.name,
            "days_evolved": days_passed,
            "changes": changes,
        }

    def _try_generate_goal_from_motivation(self, npc: NPCState,
                                            world_state: WorldState,
                                            all_npcs: dict) -> str | None:
        """[v1.2] 当 NPC 无当前目标时，从最强动机派生短期目标"""
        try:
            from .motivation import get_motivation_engine
            from .goal_evaluator import get_goal_evaluator
            mot_engine = get_motivation_engine()
            goal_engine = get_goal_evaluator()

            # 取最强动机
            top_mot = mot_engine.pick_active_motivation(npc)
            if not top_mot or top_mot.get("intensity", 0) < 40:
                return None
            # 用 goal_evaluator 的模板生成
            return goal_engine.generate_goal_from_motivation(
                npc, top_mot, world_state, all_npcs,
            )
        except Exception as e:
            logger.debug("Generate goal from motivation failed for %s: %s", npc.name, e)
            return None

    @staticmethod
    def _infer_target_location_from_goal(goal: str,
                                          world_state: WorldState) -> str:
        """[NovelRoleplay] 从目标描述推断目标地点（规则匹配）。
        如目标含"修炼"→ 山脉/洞府；"经商"→ 市场；"科举"→ 京城。
        返回 location code 或空字符串。
        """
        if not goal or not world_state:
            return ""
        locations = getattr(world_state, 'locations', None) or {}
        if not locations:
            return ""

        # 关键词 → 地点类型映射
        goal_loc_keywords = {
            "修炼": ["山", "洞", "观", "寺", "府"],
            "突破": ["山", "洞", "秘境"],
            "经商": ["市", "坊", "铺", "港"],
            "交易": ["市", "坊", "铺"],
            "科举": ["京", "府", "书院"],
            "求学": ["书院", "学", "府"],
            "寻人": ["村", "镇", "城"],
            "复仇": ["京", "城", "江湖"],
            "探险": ["遗迹", "洞", "森林", "山"],
        }

        for key, loc_keywords in goal_loc_keywords.items():
            if key in goal:
                # 在 world_state.locations 中找匹配的地点
                for code, loc_data in locations.items():
                    name = ""
                    if isinstance(loc_data, dict):
                        name = loc_data.get("location_name", loc_data.get("name", ""))
                    elif hasattr(loc_data, 'location_name'):
                        name = loc_data.location_name or ""
                    elif hasattr(loc_data, 'name'):
                        name = loc_data.name or ""
                    for lk in loc_keywords:
                        if lk in name or lk in code:
                            return code
                break
        return ""

    def batch_offline_evolve(self, npcs: list[NPCState],
                              world_state: WorldState,
                              days_passed: int = 1) -> list[dict]:
        """[NovelRoleplay] 批量轻量演化。
        用于非关键回合（日常场景），替代 batch_npc_actions 节省 LLM 调用。
        只演化已激活但今日未行动的 NPC（dormant 的不演化，由登场机制处理）。
        """
        results = []
        for npc in npcs:
            # 跳过休眠 NPC（由登场机制处理）
            if getattr(npc, 'is_dormant', False):
                continue
            # 跳过今天已行动的 NPC
            if npc.last_action_day == world_state.current_day:
                continue
            try:
                result = self.offline_evolve(npc, world_state, days_passed)
                if result["changes"]:
                    results.append(result)
            except Exception as e:
                logger.warning("offline_evolve failed for %s: %s",
                               getattr(npc, 'name', '?'), e)
        return results
