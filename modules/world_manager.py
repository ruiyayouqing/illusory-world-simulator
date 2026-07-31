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

        # [v10] 生成摘要供 LLM 使用，但保留完整对话历史
        if len(eng.narrative_history) > eng.MAX_NARRATIVE_HISTORY:
            if eng.memory_curator:
                try:
                    eng.memory_curator.generate_summary_only(
                        eng.narrative_history,
                        current_turn=eng.meta.turn_count if eng.meta else 0,
                        current_day=eng.world_state.current_day if eng.world_state else 1,
                    )
                    logger.info("Day-end summary generated (history preserved): %d entries",
                                len(eng.narrative_history))
                except Exception as e:
                    logger.warning("Day-end summary generation failed: %s", e)

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

        # [v1.5 第一期] 世界时钟推进：跨日时生成今日事件（玩家事件 + 世界事件）
        # 由 WorldTick 负责：根据玩家状态/位置计算打扰概率，roll 事件数量，
        # 从 NPC 池挑候选者，用模板生成事件摘要（不调 LLM）。
        # 玩家接受事件时才由路由层调 LLM 生成桥接叙事。
        try:
            if getattr(eng, 'world_tick', None) is not None:
                today_events = eng.world_tick.tick()
                eng.last_day_events = today_events
                logger.info("World tick day %d: %d player events, %d world events",
                            eng.world_state.current_day,
                            len(today_events.get("player_events", [])),
                            len(today_events.get("world_events", [])))
        except Exception as e:
            logger.warning("World tick failed: %s", e)

        # [v1.6] NPC-NPC 对话编排：跨日时触发 NPC 之间的自由对话
        # 收集对话会话，派发到 EventBus，供前端「江湖见闻」面板拉取
        try:
            if getattr(eng, 'npc_dialogue_manager', None) is not None:
                sessions = eng.npc_dialogue_manager.maybe_trigger_dialogues(
                    all_npcs=eng.npc_states,
                    world_state=eng.world_state,
                    player=eng.player_state,
                )
                if sessions:
                    # 派发到 EventBus，让其他子系统（如叙事引擎）可订阅
                    for sess in sessions:
                        eng.event_bus.emit("on_npc_dialogue", sess)
                    # 暂存到引擎，供 API 拉取
                    if not hasattr(eng, '_today_npc_dialogues'):
                        eng._today_npc_dialogues = []
                    eng._today_npc_dialogues.extend(sessions)
                    logger.info("[v1.6] NPC dialogues day %d: %d sessions",
                                eng.world_state.current_day, len(sessions))
        except Exception as e:
            logger.warning("[v1.6] NPC dialogue trigger failed: %s", e)

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
        # [NovelRoleplay] 先检查 dormant NPC 的登场条件
        self._check_dormant_npc_activation()
        try:
            return eng.npc_autonomous.batch_npc_actions(
                list(eng.npc_states.values()), eng.world_state, eng.player_state
            )
        except Exception as e:
            logger.warning("NPC autonomous actions failed: %s", e)
            return []

    def _check_dormant_npc_activation(self):
        """[NovelRoleplay] 检查休眠 NPC（未来角色）的登场条件。
        概率登场机制：每天有 probability_per_day 的概率主动登场。
        地点匹配时概率提升，事件触发时强制登场。

        激活后：is_dormant=False，生成"新人物出现"事件，
        将其未来关系从 is_future 标记为 active。

        去重：每个 dormant NPC 每天最多判定一次（用 last_action_day 标记）。
        """
        import random as _random
        eng = self.engine
        if not eng.npc_states or not eng.world_state:
            return
        current_day = eng.world_state.current_day
        player_loc = ""
        if eng.player_state:
            player_loc = getattr(eng.player_state, 'location', '') or ''

        activated = []
        for agent_id, npc in list(eng.npc_states.items()):
            if not getattr(npc, 'is_dormant', False):
                continue
            # [Bug] 每天最多判定一次，避免一天内多次回合重复触发
            if getattr(npc, 'last_action_day', 0) == current_day:
                continue
            cond = npc.appearance_conditions or {}
            # 检查最小天数偏移
            min_day_offset = cond.get('min_day_offset', 0)
            if current_day < min_day_offset:
                continue

            # 概率登场
            prob = cond.get('probability_per_day', 0.05)
            # 地点匹配时概率提升 3 倍（玩家走到了 NPC 所在地附近）
            cond_locations = cond.get('locations', [])
            loc_match = False
            if cond_locations and player_loc:
                for loc in cond_locations:
                    if loc and (loc in player_loc or player_loc in loc):
                        loc_match = True
                        break
            if loc_match:
                prob = min(0.5, prob * 3.0)  # 地点匹配最多 50%

            if _random.random() >= prob:
                # 即使未登场也标记今天已判定，避免同一天多次 roll
                npc.last_action_day = current_day
                continue

            # 激活 NPC
            npc.is_dormant = False
            npc.last_action_day = current_day  # 标记今天已判定
            activated.append(npc)

            # [v1.2] 休眠唤醒时的 LLM 时间跳跃推演
            # 补齐休眠期间断层：状态恢复/目标调整/位置移动/传闻听闻
            try:
                from .dormant_wake import get_dormant_wake_evaluator
                wake_eval = get_dormant_wake_evaluator()
                wake_result = wake_eval.evaluate_wake(npc, eng.world_state, engine=eng)
                if wake_result.get("evaluated"):
                    logger.info("[DormantWake] %s 苏醒推演完成（休眠%d天，降级=%s）",
                                npc.name, wake_result.get("dormant_days", 0),
                                wake_result.get("degraded", False))
            except Exception as e:
                logger.warning("[DormantWake] %s 苏醒推演失败: %s", npc.name, e)

            # 生成登场事件
            if hasattr(eng, 'event_log_today'):
                eng.event_log_today.append({
                    "type": "npc_appearance",
                    "description": f"你遇到了 {npc.name}{'（'+npc.role+'）' if npc.role else ''}",
                    "npc_name": npc.name,
                    "location": npc.current_location,
                    "day": current_day,
                })

            # 激活该 NPC 的未来关系到 GraphRAG
            if eng.graph_rag:
                for rel in eng.graph_rag.relations:
                    if getattr(rel, 'is_future', False) and (
                        rel.source == npc.name or rel.target == npc.name
                    ):
                        rel.is_future = False
                        # 标记为有效（如果之前是 future 状态）
                        if not rel.is_active:
                            rel.temporal_validity = "active"

            # [v1.3] 生成私密档案（小说模式默认启用）
            # dormant NPC 登场是天然的"信息填充"时机
            self._generate_private_facts_for_npc(npc, current_day)

            logger.info("[NovelRoleplay] dormant NPC 登场: %s (原第%d章, 地点匹配=%s)",
                        npc.name, npc.original_chapter, loc_match)

        if activated:
            logger.info("[NovelRoleplay] 本回合 %d 个未来角色登场", len(activated))

    def _generate_private_facts_for_npc(self, npc, current_day: int):
        """[v1.3] 为 NPC 生成私密档案。
        - 小说模式：dormant NPC 登场时自动调用，用原著素材填充
        - 普通模式：由开关控制，在首次接触玩家时调用
        失败时不影响主流程。"""
        try:
            eng = self.engine
            if not eng or not eng.llm:
                return
            # 已生成过则跳过
            if getattr(npc, 'private_facts_generated', False):
                return

            # 检查开关（小说模式 is_novel_roleplay 时默认启用）
            _cfg = eng._load_config() if hasattr(eng, '_load_config') else {}
            is_novel = bool(getattr(eng, 'is_novel_roleplay', False)
                           or getattr(eng, '_is_novel_mode', False))
            if not is_novel:
                # 普通模式：检查开关
                feat_cfg = _cfg.get("features", {}).get("npc_private_facts", {})
                if not feat_cfg.get("enabled", False):
                    return

            from modules.npc_private_facts import generate_private_facts, get_max_facts

            max_facts = get_max_facts(_cfg)

            # 世界背景
            world_context = ""
            if eng.world_state:
                world_context = f"世界：{eng.world_state.world_name or ''}\n"
                if eng.world_state.description:
                    world_context += f"背景：{eng.world_state.description[:300]}"

            # 小说原著素材（从 GraphRAG 提取该 NPC 的相关关系/事件）
            novel_source = ""
            if is_novel and eng.graph_rag:
                try:
                    rels = []
                    for rel in eng.graph_rag.relations:
                        if (getattr(rel, 'source', '') == npc.name
                            or getattr(rel, 'target', '') == npc.name):
                            rels.append(
                                f"{getattr(rel, 'source', '')} → {getattr(rel, 'target', '')}: "
                                f"{getattr(rel, 'relation_type', '')} {getattr(rel, 'description', '')}"
                            )
                    if rels:
                        novel_source = "该角色在原著中的关系：\n" + "\n".join(rels[:8])
                except Exception:
                    pass

            facts = generate_private_facts(
                npc, eng.llm,
                world_context=world_context,
                novel_source=novel_source,
                max_facts=max_facts,
            )

            # 覆写 created_day
            for f in facts:
                f["created_day"] = current_day

            if facts:
                logger.info(
                    "[PrivateFacts] %s 登场时生成 %d 条私密档案",
                    npc.name, len(facts)
                )
        except Exception as e:
            logger.warning(
                "[PrivateFacts] 生成失败 (%s): %s",
                getattr(npc, 'name', '?'), e
            )

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
