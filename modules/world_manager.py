"""
[v9] 世界管理器 — 负责世界状态的演化和管理。

从 GameEngine.advance_time / _on_new_day 中抽取，职责：
1. 时间推进（时段/天/季节）
2. NPC批量演化（离线行为、感知、自主行动）
3. 经济系统更新
4. 势力战争推进
5. 年度NPC生命演化
6. 新一天重置逻辑
"""
from __future__ import annotations
import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .game_engine import GameEngine

logger = logging.getLogger("chronoverse.world_manager")


class WorldManager:
    """世界管理器：管理世界状态的时序演化"""

    def __init__(self, engine: "GameEngine"):
        self.engine = engine

    def advance_time(self, time_slot: str = None) -> dict:
        """
        推进世界时间，触发NPC演化、经济更新、势力战争等。
        """
        eng = self.engine
        if not eng.world_agent or not eng.world_state or not eng.age_system:
            return {}

        result = {
            "time_events": [], "age_result": None, "npc_events": [],
            "war_events": [], "regret": None,
        }

        # Step 1: 推进时间
        time_result = eng.age_system.advance_time(eng.world_state)
        result["time_events"] = time_result.get("events", [])

        # Step 2: 玩家老化
        if eng.player_state:
            age_result = eng.age_system.age_player(eng.player_state, eng.world_state)
            result["age_result"] = age_result

        # Step 3: NPC演化（[v11] 统一使用 NPCAgent.batch_evolve，
        # 弃用随机二选一逻辑，确保 NPC 行为一致可预测）
        if eng.npc_states:
            npc_events = self._evolve_npcs()
            result["npc_events"] = npc_events
            if not npc_events:
                # 回退：NPCAgent 不可用时尝试 NpcAutonomous
                npc_actions = self._run_npc_autonomous()
                if npc_actions:
                    result["npc_events"] = npc_actions

            # NPC睡眠模拟
            sleeping_events = self._simulate_sleeping_npcs()
            if sleeping_events:
                result["sleeping_npc_events"] = sleeping_events

        # Step 4: 经济更新
        self._update_economy()

        # Step 5: 势力战争
        war_events = self._advance_wars()
        result["war_events"] = war_events

        # Step 6: 命运遗憾
        if eng.destiny_regret and eng.player_state:
            regret = eng.destiny_regret.check_regret(eng.player_state, eng.world_state)
            if regret:
                result["regret"] = regret

        # Step 7: 新一天处理
        if time_result.get("new_day"):
            self.on_new_day()

        # Step 8: 年度NPC生命演化
        yearly = self._maybe_yearly_evolution()
        if yearly:
            result["yearly_evolution"] = yearly

        return result

    def on_new_day(self):
        """新一天开始时的重置和自动生成"""
        eng = self.engine

        # 重置日志
        eng.event_log_today = []
        eng.action_log_today = []
        eng.player_impacts_today = []
        eng.world_changes_today = []

        # 玩家休息恢复
        if eng.player_state and eng.player_agent:
            eng.player_agent.rest(eng.player_state)

        # [v9] 限制叙事历史大小，防止内存无限增长
        # [Bug] 截断前先调用 Curator 压缩旧条目，避免关键剧情直接丢失
        if len(eng.narrative_history) > eng.MAX_NARRATIVE_HISTORY:
            # 先尝试用 Curator 压缩超出阈值的部分
            if eng.memory_curator:
                try:
                    # 计算需要压缩多少条：超出 KEEP 的部分
                    excess = len(eng.narrative_history) - eng.NARRATIVE_HISTORY_KEEP
                    # 每次压缩 summary_interval(10) 条为 1 条，循环压缩直到低于阈值
                    compress_rounds = 0
                    while (len(eng.narrative_history) > eng.NARRATIVE_HISTORY_KEEP
                           and compress_rounds < 20):  # 安全上限，防止无限循环
                        summary_result = eng.memory_curator.summarize_history(
                            eng.narrative_history,
                            current_turn=eng.meta.turn_count if eng.meta else 0,
                            current_day=eng.world_state.current_day if eng.world_state else 1,
                        )
                        if summary_result.get("status") == "skipped":
                            break  # Curator 跳过（条目不足或刚压缩过）
                        # 替换 narrative_history
                        eng.narrative_history = (
                            summary_result.get("replacement", [])
                            + summary_result.get("remaining", [])
                        )
                        eng._narrative_compressed = True
                        compress_rounds += 1
                    logger.info("Narrative history compressed %d rounds, now %d entries",
                                compress_rounds, len(eng.narrative_history))
                except Exception as e:
                    logger.warning("Curator compression before trim failed: %s", e)

            # 如果压缩后仍超过阈值，才执行硬截断（保留最近的条目）
            if len(eng.narrative_history) > eng.MAX_NARRATIVE_HISTORY:
                eng.narrative_history = eng.narrative_history[-eng.NARRATIVE_HISTORY_KEEP:]
                logger.warning("Narrative history hard-trimmed to %d entries (Curator insufficient)",
                               eng.NARRATIVE_HISTORY_KEEP)

        # 自动日终小说生成
        if eng.last_novel_checkpoint < len(eng.narrative_history) - 2:
            try:
                eng.generate_novel_chapter()
            except Exception as e:
                logger.warning("Novel chapter generation failed: %s", e)

        # [v10++] NPC 反思（Generative Agents 式）：新一天开始时，NPC 回顾近期记忆生成洞察
        # 内部有节流（每 N 天一次）和数量限制（max_npcs），失败不影响主流程
        # [优化] NPC 反思耗时长（最多10个NPC串行调用），改为后台异步执行
        try:
            if hasattr(eng, 'task_queue') and eng.task_queue is not None:
                eng.task_queue.post(eng.trigger_npc_reflection)
            else:
                eng.trigger_npc_reflection()
        except Exception as e:
            logger.warning("NPC reflection on new day failed: %s", e)

    def _evolve_npcs(self) -> list:
        """NPC批量演化"""
        eng = self.engine
        if not eng.npc_agent:
            return []
        try:
            return eng.npc_agent.batch_evolve(
                list(eng.npc_states.values()), eng.world_state, eng.player_state
            )
        except Exception as e:
            logger.warning("NPC batch evolve failed: %s", e)
            return []

    def _run_npc_autonomous(self) -> list:
        """NPC自主行动"""
        eng = self.engine
        if not eng.npc_autonomous:
            return []
        try:
            return eng.npc_autonomous.batch_npc_actions(
                list(eng.npc_states.values()), eng.world_state, eng.player_state
            )
        except Exception as e:
            logger.warning("NPC autonomous actions failed: %s", e)
            return []

    def _simulate_sleeping_npcs(self) -> list:
        """模拟睡眠中的NPC"""
        eng = self.engine
        if not (eng.npc_perception and eng.player_state):
            return []

        sleeping_events = []
        eng.npc_perception.batch_classify(
            list(eng.npc_states.values()), eng.player_state, eng.world_state
        )
        for npc in eng.npc_states.values():
            if eng.npc_perception.should_simulate(npc.agent_id, eng.world_state):
                event = eng.npc_perception.simulate_sleeping_npc(npc, eng.world_state)
                if event:
                    sleeping_events.append(event)
        return sleeping_events

    def _update_economy(self):
        """更新经济系统"""
        eng = self.engine
        if eng.world_state and eng.world_state.economy and eng.economy_system:
            eng.economy_system.update_prices(eng.world_state.economy, eng.world_state)

    def _advance_wars(self) -> list:
        """推进势力战争"""
        eng = self.engine
        if not eng.faction_wars:
            return []

        war_events = []
        war_triggers = eng.faction_wars.check_war_triggers(eng.world_state)
        war_events.extend(war_triggers)

        for war in list(eng.faction_wars.active_wars):
            war_event = eng.faction_wars.advance_war(war, eng.world_state)
            if war_event:
                war_events.append(war_event)

        return war_events

    def _maybe_yearly_evolution(self) -> Optional[list]:
        """年度NPC生命演化（每年仅一次）"""
        eng = self.engine
        if not (eng.npc_life_evolution and eng.npc_states and eng.world_state):
            return None

        current_day = eng.world_state.current_day
        if current_day - eng._last_year_evolved < 365:
            return None

        eng._last_year_evolved = current_day
        known_locations = list(eng.world_state.locations.keys()) if eng.world_state.locations else []
        try:
            year_events = eng.npc_life_evolution.evolve_year(
                eng.npc_states, eng.world_state, known_locations
            )
        except Exception as e:
            logger.warning("evolve_year failed: %s", e)
            return None
        return year_events[:10] if year_events else None
