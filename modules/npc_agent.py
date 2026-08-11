from __future__ import annotations
import random
import logging
from typing import TYPE_CHECKING

from .agent_base import BaseAgent
from .schemas import NPCState, WorldState, PlayerState, RelationEntry
from .prompt.npc_prompts import (
    NPC_OFFLINE_EVOLUTION_PROMPT, NPC_INTERACTION_PROMPT,
    NPC_RELATION_UPDATE_PROMPT,
)
from .prompt_utils import resolve_location_name, sanitize_player_input  # [Bug] location code → display name | [v1.4 P2-10] Prompt injection 防护
from .npc_autonomous import NpcAutonomous  # [Bug] 复用 _resolve_location_to_code
from .mbti_styles import (
    get_mbti_profile, get_decision_style_prompt,
    modify_social_chance, modify_exploration_chance,
    assign_mbti_to_npc, mbti_to_decision_style,
)
from .npc_lod import classify_tier, procedural_evolve as lod_procedural_evolve
from .daily_routine import get_routine_hint as _get_routine_hint

if TYPE_CHECKING:
    from .llm.base_llm import BaseLLM
    from .branch_planner import BranchPlanner

logger = logging.getLogger("chronoverse.npc_agent")


class NPCAgent(BaseAgent):
    """NPC Agent: inherit BaseAgent, integrate BranchPlanner and MBTI"""

    def __init__(self, llm: BaseLLM, planner: BranchPlanner = None,
                 procedural_memory=None, planner_probability: float = 0.0):
        super().__init__(llm, memory=None, lorebook=None)
        self.planner = planner
        # [v10] NPC 程序性记忆引用
        self.procedural_memory = procedural_memory
        # [v10++] 角色动态状态管理器引用（CHIRON 式），可选
        # 由 GameEngine._init_services 注入，用于在 NPC 独立交互时注入动态状态
        self.character_state_manager = None
        # [v10++] NPC 反思管理器引用（Generative Agents 式），可选
        # 由 GameEngine._init_services 注入，用于在构建决策 prompt 时注入反思洞察
        self.reflection_manager = None
        # [v10++] NPC 技能自学库引用（Voyager/Hermes 式），可选
        # 由 GameEngine._init_services 注入，用于在决策时注入可用技能、
        # 并在行动成功/失败后学习或记录失败
        self.skill_library = None
        # [v1.6 P1-8] 情感记忆管理器引用由基类 BaseAgent 提供
        # （emotional_memory_manager 字段 + set_emotional_memory_manager 方法）
        # [F] BranchPlanner 在 offline_evolve 中的触发概率（0.0=禁用，0.05=5%概率）
        # 原先硬编码为 0（random.random() < 0 永远为 False），现改为 config 可调
        # 注意：BranchPlanner.plan() 每次调用需要 20-30 秒，11 个 NPC 即使 5% 概率
        # 也可能增加 1-2 秒平均延迟。建议关键剧情才启用，日常保持 0.0
        self.planner_probability = float(planner_probability)

    def _get_reflection_insights(self, npc: NPCState) -> str:
        """[v10++] 获取 NPC 的反思洞察文本，用于注入决策 prompt。
        若反思管理器不可用或无洞察，返回空字符串。"""
        if not self.reflection_manager:
            return ""
        try:
            return self.reflection_manager.get_insights_for_prompt(npc.agent_id, top_k=3)
        except Exception as e:
            logger.debug("获取 NPC %s 反思洞察失败: %s", npc.name, e)
            return ""

    def _get_skills_for_prompt(self, npc: NPCState, context: str = "") -> str:
        """[v10++] 获取 NPC 可用技能文本，用于注入决策 prompt（Voyager/Hermes 式）。
        若技能库不可用或无相关技能，返回空字符串。失败时不影响主流程。"""
        if not self.skill_library:
            return ""
        try:
            # 上下文为空时用 NPC 当前位置/目标作为检索依据
            search_ctx = context or f"{npc.current_location or ''} {npc.ai_behavior.get('current_goal', '')}"
            return self.skill_library.get_skills_for_prompt(npc.agent_id, search_ctx, top_k=3)
        except Exception as e:
            logger.debug("获取 NPC %s 可用技能失败: %s", npc.name, e)
            return ""

    def _get_emotion_hint(self, npc: NPCState) -> str:
        """[v1.6 P1-8] 获取 NPC 当前情感状态提示文本。
        由 EmotionalMemoryManager 生成 8 维向量主导情感 + 风格提示。
        若情感管理器不可用或情感强度低于阈值，返回空字符串。"""
        if not self.emotional_memory_manager:
            return ""
        try:
            return self.emotional_memory_manager.get_npc_emotion_hint(
                npc.agent_id, npc.name)
        except Exception as e:
            logger.debug("获取 NPC %s 情感状态失败: %s", npc.name, e)
            return ""

    def plan_next_action(self, npc: NPCState, world_state: WorldState,
                         context: dict = None) -> dict:
        # [v10] 先查询程序性记忆获取经验建议
        procedural_suggestions = []
        if self.procedural_memory:
            procedural_suggestions = self.procedural_memory.get_action_suggestions(
                npc, world_state
            )

        if self.planner:
            try:
                plan_result = self.planner.plan(npc, world_state)
                if plan_result.actions:
                    if npc.mbti_type:
                        plan_result.actions = self._apply_mbti_to_actions(
                            plan_result.actions, npc.mbti_type)
                    # [v10] 用程序性记忆调整动作优先级
                    if procedural_suggestions:
                        plan_result.actions = self._apply_procedural_memory(
                            plan_result.actions, procedural_suggestions
                        )
                    return {
                        "actions": plan_result.actions,
                        "selected_branch": plan_result.selected_branch.branch_type if plan_result.selected_branch else "unknown",
                        "replan_count": plan_result.replan_count,
                        "planned": True,
                        "procedural_suggestions": procedural_suggestions[:3],
                    }
            except Exception as e:
                logger.warning("NPC %s plan failed, fallback: %s", npc.name, e)
        try:
            return self._single_step_plan(npc, world_state)
        except Exception as e:
            logger.warning("NPC %s fallback plan failed: %s", npc.name, e)
            return {"actions": [{"type": "rest", "detail": "休息", "energy_cost": 0}],
                    "selected_branch": "rest", "replan_count": 0, "planned": False}

    def execute_action(self, action: dict, npc: NPCState,
                       world_state: WorldState) -> dict:
        action_type = action.get("type", "idle")
        detail = action.get("detail", "")
        energy_cost = action.get("energy_cost", 5)
        if action_type == "rest":
            npc.stats.energy = min(100, npc.stats.energy + 20)
        else:
            npc.stats.energy = max(0, npc.stats.energy - energy_cost)
        npc.recent_actions.append({
            "day": world_state.current_day, "action": action_type,
            "detail": detail[:200], "location": npc.current_location,
        })
        if len(npc.recent_actions) > 10:
            npc.recent_actions = npc.recent_actions[-10:]
        return {"action": action_type, "detail": detail,
                "energy_cost": energy_cost, "npc_id": npc.agent_id}

    def offline_evolve(self, npc: NPCState, world_state: WorldState,
                       player: PlayerState = None) -> dict:
        # [F] BranchPlanner 触发概率从 config 读取（npc.planner_probability）
        # 原先硬编码为 random.random() < 0（永远 False），现已改为可配置
        # 注意：plan() 每次调用需要 20-30 秒，11 个 NPC 即使 5% 概率也可能增加延迟
        # 建议日常保持 0.0，仅在需要更智能 NPC 行为时调到 0.02-0.05
        if self.planner and self.planner_probability > 0 and random.random() < self.planner_probability:
            try:
                plan_result = self.planner.plan(npc, world_state)
                if plan_result.actions:
                    action = plan_result.actions[0]
                    result = self.execute_action(action, npc, world_state)
                    return {
                        "npc_id": npc.agent_id, "npc_name": npc.name,
                        "action": result["action"], "detail": result["detail"],
                        "location": npc.current_location, "mood_change": 0,
                        "branch": plan_result.selected_branch.branch_type if plan_result.selected_branch else "",
                    }
            except Exception as e:
                logger.warning("NPC %s offline evolve plan failed: %s", npc.name, e)
        return self._single_step_evolve(npc, world_state, player)

    def interact_with_player(self, npc: NPCState, player: PlayerState,
                             player_action: str, world_state: WorldState) -> dict:
        favor = npc.relation_to_player.favor
        relation_desc = npc.relation_to_player.description or "stranger"
        mbti_hint = ""
        if npc.mbti_type:
            mbti_hint = f"\n{get_decision_style_prompt(npc.mbti_type)}"
        # [v10++] 注入 NPC 动态状态（CHIRON 式）
        dynamic_state_hint = ""
        if self.character_state_manager:
            state_text = self.character_state_manager.get_state_for_prompt(npc.agent_id)
            if state_text:
                dynamic_state_hint = f"\n【该角色当前动态状态】\n{state_text}"
        # [v10++] 注入 NPC 反思洞察（Generative Agents 式）
        reflection_hint = self._get_reflection_insights(npc)
        # [v1.4 P2-10] Prompt injection 防护
        safe_action = sanitize_player_input(player_action)
        prompt = NPC_INTERACTION_PROMPT.format(
            npc_name=npc.name, npc_age=npc.age, personality=npc.personality,
            favor=favor, relation=relation_desc, player_name=player.name,
            player_action=safe_action, day=world_state.current_day,
            time=world_state.current_time,
        )
        if mbti_hint:
            prompt += mbti_hint
        if dynamic_state_hint:
            prompt += dynamic_state_hint
        if reflection_hint:
            prompt += f"\n{reflection_hint}"
        # [v10++] 注入 NPC 可用技能（Voyager/Hermes 式）
        skills_hint = self._get_skills_for_prompt(npc, context=player_action)
        if skills_hint:
            prompt += f"\n{skills_hint}"
        # [v1.6 P1-8] 注入 NPC 当前情感状态（影响对话语气与决策倾向）
        emotion_hint = self._get_emotion_hint(npc)
        if emotion_hint:
            prompt += f"\n{emotion_hint}"
        response = self.llm.chat_json(prompt, temperature=0.7)
        dialogue = response.get("dialogue", f"{npc.name} silent.")
        favor_change = int(response.get("favor_change", 0) or 0)
        action = response.get("npc_action", "idle")
        player_gift = response.get("player_gift", None)
        npc.relation_to_player.favor = max(0, min(100, favor + favor_change))
        npc.relation_to_player.interaction_count += 1
        npc.relation_to_player.last_interaction = f"day{world_state.current_day}"
        return {
            "npc_id": npc.agent_id, "dialogue": dialogue,
            "favor_change": favor_change, "action": action,
            "player_gift": player_gift, "new_favor": npc.relation_to_player.favor,
        }

    def update_player_relation(self, npc: NPCState, player: PlayerState,
                               favor_change: int, reason: str = ""):
        npc.relation_to_player.favor = max(0, min(100,
            npc.relation_to_player.favor + favor_change))

    def batch_evolve(self, npcs: list[NPCState], world_state: WorldState,
                     player: PlayerState = None) -> list[dict]:
        events = []
        current_day = world_state.current_day
        current_time = getattr(world_state, "current_time", "")
        # [v1.2] 时序轮询：每个 NPC 每个时段最多行动1次
        # turn_id 格式 "day:time_slot"，如 "5:上午"
        turn_id = f"{current_day}:{current_time}"
        all_npcs_ref = getattr(world_state, "_all_npcs_ref", None) or {}
        # [v1.2] AI LOD 统计：core 走 LLM，secondary 走 CPU 程序化推演
        lod_stats = {"core": 0, "secondary": 0, "skipped": 0}
        for npc in npcs:
            # [v1.2] 时序轮询：按时段限制（取代原 last_action_day 每日限制）
            # 让 NPC 在不同时段都能行动，配合 daily_routine 实现时段感知
            if npc.last_action_turn == turn_id:
                continue
            # [NovelRoleplay] 跳过休眠 NPC（未来角色未登场前不参与自主行动）
            # 与 schemas.py 注释及 NpcAutonomous.batch_npc_actions 行为保持一致
            # [功能二] 跳过玩家手动隐藏的 NPC
            if getattr(npc, 'is_dormant', False) or getattr(npc, 'hidden', False):
                lod_stats["skipped"] += 1
                continue

            # [v1.2] AI LOD 分层：判定 NPC 走 LLM 还是 CPU 推演
            tier = classify_tier(npc, player, world_state)
            lod_stats[tier] += 1

            if tier == "secondary":
                # 次要 NPC：CPU 程序化推演（不调用 LLM）
                # [v1.2] 时序轮询后概率调高：50%→40%（每时段 40% ≈ 每天 2.4 次）
                # 无 LLM 成本，让 NPC 行为更活跃、更有时序感
                if random.random() >= 0.4:
                    continue
                try:
                    event = lod_procedural_evolve(
                        npc, world_state, player, all_npcs_ref,
                    )
                    npc.last_action_turn = turn_id
                    npc.last_action_day = current_day
                    # CPU 推演结果同样经 ActionArbiter 裁决（保持一致性）
                    event = self._arbitrate_action(
                        event, npc, world_state, all_npcs_ref, player,
                    )
                    event["lod_tier"] = "secondary"
                    events.append(event)
                except Exception as e:
                    logger.warning("NPC %s (secondary) procedural evolve failed: %s",
                                   npc.name, e)
                continue

            # 核心 NPC：走 LLM 思考循环
            # [v1.2] 时序轮询后概率降低：30%→15%（每时段 15% ≈ 每天 0.9 次）
            # 控制 LLM 成本，避免 6 时段轮询导致 token 暴涨
            if random.random() < 0.15:
                try:
                    event = self.offline_evolve(npc, world_state, player)
                    npc.last_action_turn = turn_id
                    npc.last_action_day = current_day
                    # [v1.2] 行动经 ActionArbiter 裁决（对抗性行动走 dice_roll）
                    # 裁决会应用状态变化、emit 事件到 EventBus 触发同场 NPC 反应
                    event = self._arbitrate_action(event, npc, world_state, all_npcs_ref, player)
                    event["lod_tier"] = "core"
                    events.append(event)
                except Exception as e:
                    logger.warning("NPC %s (core) evolve failed: %s", npc.name, e)

        # [v1.2] LOD 统计日志（INFO 级别，便于观察 LLM 调用削减效果）
        logger.info(
            "[LOD] turn=%s core=%d secondary=%d skipped=%d events=%d",
            turn_id, lod_stats["core"], lod_stats["secondary"],
            lod_stats["skipped"], len(events),
        )
        return events

    def _arbitrate_action(self, event: dict, npc: NPCState,
                           world_state: WorldState, all_npcs: dict,
                           player: PlayerState = None) -> dict:
        """[v1.2] 把 NPC 行动送到 ActionArbiter 裁决。

        对抗性行动（刺杀/挑战/偷窃等）走 dice_roll 判定；
        非对抗性行动（移动/修炼/独处）仅做可行性检查。
        裁决结果合并回 event，供上层叙事引用。
        """
        try:
            from .action_arbiter import get_action_arbiter
            arbiter = get_action_arbiter()
            # 适配 action dict 字段：npc_agent 用 'action'，arbiter 用 'action_type'
            action_dict = {
                "action_type": event.get("action", "idle"),
                "target_name": event.get("target", ""),
                "target_id": event.get("target_id", ""),
                "location": npc.current_location,
                "detail": event.get("detail", ""),
                "destination": event.get("destination", ""),
            }
            verdict = arbiter.adjudicate(
                action=action_dict, actor=npc, world_state=world_state,
                all_npcs=all_npcs, player=player,
            )
            # 合并裁决结果到 event
            event["arbitrated"] = True
            event["executed"] = verdict.executed
            event["severity"] = verdict.severity
            event["narrative_summary"] = verdict.narrative_summary
            if not verdict.executed:
                event["detail"] = f"{verdict.narrative_summary}（未成行：{verdict.feasible_reason}）"
            else:
                event["detail"] = verdict.narrative_summary
        except Exception as e:
            logger.debug("Action arbitration skipped for %s: %s", npc.name, e)
        return event

    def assign_mbti(self, npc: NPCState):
        if not npc.mbti_type:
            npc.mbti_type = assign_mbti_to_npc(npc.personality, npc.tags)
        # [Bug] assign_mbti 原先只设 mbti_type 不更新 decision_style，
        #       导致已有NPC即使被分配了MBTI类型，decision_style 仍为默认 "normal"。
        #       现在同步更新 decision_style，使其与 MBTI 类型保持一致。
        if npc.ai_behavior.get("decision_style", "normal") == "normal":
            npc.ai_behavior["decision_style"] = mbti_to_decision_style(npc.mbti_type)

    def _single_step_plan(self, npc: NPCState, world_state: WorldState) -> dict:
        prompt = NPC_OFFLINE_EVOLUTION_PROMPT.format(
            npc_name=npc.name, npc_age=npc.age, personality=npc.personality,
            tags=", ".join(npc.tags), current_location=resolve_location_name(npc.current_location or "unknown", world_state),  # [Bug] location code → display name
            current_goal=npc.ai_behavior.get("current_goal", "live"),
            decision_style=npc.ai_behavior.get("decision_style", "normal"),
            day=world_state.current_day, time=world_state.current_time,
            weather=world_state.weather, season=world_state.season,
        )
        if npc.mbti_type:
            prompt += f"\n{get_decision_style_prompt(npc.mbti_type)}"
        # [G] 注入 NPC 长期目标（让行为有连贯性，而不是每天随机吃饭/练功/闲逛）
        # long_term_goal 来自 ai_behavior，由 NPCSpawner 生成或 LLM 演化时提取
        long_term_goal = npc.ai_behavior.get("long_term_goal", "")
        if long_term_goal:
            prompt += (
                f"\n【长期目标 - 重要】{npc.name}的长期目标是「{long_term_goal}」。"
                f"今天的行动应该服务于这个长期目标，即使短期内看似无关，"
                f"也要让行动方向与长期目标保持一致。"
            )
        # [v1.2] 注入职业日程提示（让 LLM 决策时考虑当前时段的职业惯例）
        # LLM 仍可基于目标/处境调整，日程仅作参考
        try:
            routine_hint = _get_routine_hint(npc, world_state)
            if routine_hint:
                prompt += f"\n{routine_hint}"
        except Exception:
            pass
        # [v10++] 注入 NPC 反思洞察（Generative Agents 式）
        reflection_hint = self._get_reflection_insights(npc)
        if reflection_hint:
            prompt += f"\n{reflection_hint}"
        # [v10++] 注入 NPC 可用技能（Voyager/Hermes 式）
        skills_hint = self._get_skills_for_prompt(npc)
        if skills_hint:
            prompt += f"\n{skills_hint}"
        # [v1.6 P1-8] 注入 NPC 当前情感状态（Plutchik 8 维向量）
        emotion_hint = self._get_emotion_hint(npc)
        if emotion_hint:
            prompt += f"\n{emotion_hint}"
        # [v10] 优先使用结构化输出（NPC 行动 schema），失败回退到 chat_json
        if hasattr(self.llm, "chat_structured"):
            try:
                response = self.llm.chat_structured(prompt, "npc_action", temperature=0.7)
            except Exception as e:
                logger.warning("Structured NPC action failed, fallback: %s", e)
                response = self.llm.chat_json(prompt, temperature=0.7)
        else:
            response = self.llm.chat_json(prompt, temperature=0.7)
        action = response.get("action", "idle")
        # 兼容 schema 的 description 字段与原 prompt 的 detail 字段
        detail = response.get("detail") or response.get("description", "")
        return {
            "actions": [{"type": action, "detail": detail, "energy_cost": 10}],
            "selected_branch": action, "replan_count": 0, "planned": False,
        }

    def _single_step_evolve(self, npc: NPCState, world_state: WorldState,
                             player: PlayerState = None) -> dict:
        plan = self._single_step_plan(npc, world_state)
        actions = plan.get("actions", [{}])
        action = actions[0] if actions else {}
        action_type = action.get("type", "idle")
        detail = action.get("detail", "")
        energy_cost = action.get("energy_cost", 10)
        new_location = action.get("new_location", npc.current_location)
        # [Bug] 验证 new_location 是否为世界中存在的地点，防止 LLM 幻觉导致 NPC 移动到无效位置
        # LLM 返回的可能是显示名或 code，需要反向查找为 location code
        if new_location and new_location != npc.current_location:
            resolved_code = NpcAutonomous._resolve_location_to_code(new_location, world_state)
            if resolved_code:
                npc.current_location = resolved_code
        # [Bug#11] 扣除体力，与 execute_action 保持一致
        if action_type == "rest":
            npc.stats.energy = min(100, npc.stats.energy + 20)
        else:
            npc.stats.energy = max(0, npc.stats.energy - energy_cost)
        npc.recent_actions.append({
            "day": world_state.current_day, "action": action_type,
            "detail": detail, "location": npc.current_location,
        })
        if len(npc.recent_actions) > 10:
            npc.recent_actions = npc.recent_actions[-10:]
        return {
            "npc_id": npc.agent_id, "npc_name": npc.name,
            "action": action_type, "detail": detail,
            "location": npc.current_location, "mood_change": 0,
        }

    def _apply_mbti_to_actions(self, actions: list[dict], mbti_type: str) -> list[dict]:
        profile = get_mbti_profile(mbti_type)
        if not profile:
            return actions
        modified = []
        for action in actions:
            a = dict(action)
            if a.get("type") in ("explore", "trade") and profile.risk_tolerance > 0.6:
                a["priority"] = a.get("priority", 0.5) * 1.2
            if a.get("type") == "social" and profile.social_frequency < 0.3:
                a["priority"] = a.get("priority", 0.5) * 0.5
            if profile.planning_horizon == "short" and a.get("energy_cost", 0) > 30:
                a["priority"] = a.get("priority", 0.5) * 0.7
            modified.append(a)
        return modified

    def _apply_procedural_memory(self, actions: list[dict],
                                  suggestions: list[dict]) -> list[dict]:
        """
        [v10] 用程序性记忆调整动作优先级。
        历史上有效的动作获得优先级加成，历史上低效的动作被降级。
        """
        if not suggestions:
            return actions

        # 构建 action_type -> effectiveness 映射
        effectiveness_map = {
            s["action_type"]: s["effectiveness"]
            for s in suggestions
        }

        modified = []
        for action in actions:
            a = dict(action)
            action_type = a.get("type", "idle")
            if action_type in effectiveness_map:
                eff = effectiveness_map[action_type]
                # 高有效性动作获得加成，低有效性动作被降级
                if eff >= 0.7:
                    a["priority"] = a.get("priority", 0.5) * (1.0 + eff * 0.3)
                    a["procedural_hint"] = f"历史有效性{eff:.0%}"
                elif eff < 0.4:
                    a["priority"] = a.get("priority", 0.5) * 0.7
                    a["procedural_hint"] = f"历史有效性较低{eff:.0%}"
            modified.append(a)

        # 按优先级排序
        modified.sort(key=lambda x: x.get("priority", 0.5), reverse=True)
        return modified

    # ── [v10++] NPC 技能自学库（Voyager/Hermes 式） ─────────

    def learn_skill_from_success(self, npc: NPCState, context: str,
                                  action: str, result: str,
                                  turn: int, day: int):
        """[v10++] 从成功交互中学习技能（Voyager/Hermes 式）。
        委托给技能自学库，失败时不影响主流程。"""
        if not self.skill_library:
            return None
        try:
            return self.skill_library.learn_from_success(
                npc.agent_id, npc.name, context, action, result, turn, day
            )
        except Exception as e:
            logger.warning("NPC %s 技能学习失败: %s", npc.name, e)
            return None

    def record_skill_failure(self, npc: NPCState, skill_id: str, turn: int):
        """[v10++] 记录技能使用失败（Voyager/Hermes 式）。
        委托给技能自学库，失败时不影响主流程。"""
        if not self.skill_library:
            return
        try:
            self.skill_library.record_failure(npc.agent_id, skill_id, turn)
        except Exception as e:
            logger.warning("NPC %s 技能失败记录失败: %s", npc.name, e)
