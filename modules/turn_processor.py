"""
[v9] 回合处理器 — 负责玩家输入的完整处理流程。

从 GameEngine.process_player_input 中抽取，职责：
1. 玩家意图解析 + 叙事生成（流式/非流式）
2. 骰子判定
3. 蝴蝶效应评估
4. 身份审计
5. 经验/等级处理
6. 记忆更新（RAG + GraphRAG）
7. 自动存档

所有子系统交互通过 EventBus 解耦。
"""
from __future__ import annotations
import logging
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .game_engine import GameEngine

logger = logging.getLogger("chronoverse.turn_processor")

# 自杀关键词列表
SUICIDE_KEYWORDS = [
    "自杀", "自尽", "自刎", "自戕", "割腕", "服毒", "上吊", "投河",
    "跳崖", "抹脖子", "了断自己", "结束自己的生命", "寻死",
]

# 伏笔关键词
FORESHADOW_KEYWORDS = [
    "秘密", "阴谋", "伏笔", "暗中", "真相", "隐藏", "背叛", "神秘",
]


class TurnProcessor:
    """回合处理器：处理一次玩家输入的完整生命周期"""

    def __init__(self, engine: "GameEngine"):
        self.engine = engine

    def process(self, player_input: str) -> dict:
        """
        处理玩家输入，返回完整响应。
        
        这是从 GameEngine.process_player_input 抽取的核心逻辑。
        """
        eng = self.engine

        if not eng.player_agent or not eng.player_state:
            raise RuntimeError("游戏未初始化")

        # 更新回合计数
        eng.meta.current_turn += 1
        eng.meta.save_timestamp = datetime.now().isoformat()

        is_suicide = any(kw in player_input for kw in SUICIDE_KEYWORDS)
        npc_names = [npc.name for npc in eng.npc_states.values()] if eng.npc_states else []

        # 构建固定prompt
        fixed_prompt = eng._get_fixed_prompt()
        time_context = eng._get_time_context()
        if time_context:
            fixed_prompt = fixed_prompt + "\n\n" + time_context if fixed_prompt else time_context

        # Step 1: 生成叙事（流式/非流式）
        narrative, options, response = self._generate_narrative(
            player_input, npc_names, fixed_prompt
        )

        # Step 2: 骰子判定
        dice_result = self._handle_dice(response)

        # Step 3: 更新记忆
        if narrative:
            eng.action_log_today.append(narrative[:300])
            eng.player_agent.update_memory(
                eng.player_state, narrative[:400],
                eng.world_state.current_day if eng.world_state else 1
            )

        # Step 4: 叙事时间感知
        time_skip_result, year_evolution_events = self._handle_time_perception(
            narrative, player_input
        )

        # Step 5: 蝴蝶效应
        impact, world_event = self._handle_butterfly(player_input, narrative, response)

        # Step 6: 死亡检测
        death, suicide_confirm = self._handle_death(is_suicide)

        # Step 7: RAG记忆存储
        self._store_to_rag(narrative, player_input)

        # Step 8: GraphRAG构建
        self._build_graph_rag(narrative)

        # Step 9: 身份审计
        audit_results = self._run_identity_audit(narrative)

        # Step 10: 经验/等级处理
        exp_result = self._handle_experience(player_input, narrative)

        # Step 11: 记录叙事历史
        self._record_history(narrative, player_input, world_event)

        # Step 12: 自动存档
        eng.save_game("auto")

        return {
            "narrative": narrative,
            "options": options,
            "dice_result": dice_result,
            "status_changes": response.get("status_changes", {}),
            "new_effects": response.get("new_effects", []),
            "removed_effects": response.get("removed_effects", []),
            "world_event": world_event,
            "auto_event": None,  # 由原engine的_maybe_trigger_world_event处理
            "impact": impact,
            "death": death,
            "suicide_confirm": suicide_confirm,
            "identity_log": response.get("_identity_log", []),
            "audit_results": audit_results,
            "auto_image": eng._maybe_auto_image(narrative),
            "time_skip": time_skip_result,
            "year_evolution": year_evolution_events if year_evolution_events else None,
        }

    def _generate_narrative(self, player_input: str, npc_names: list,
                            fixed_prompt: str) -> tuple:
        """生成叙事和选项"""
        eng = self.engine
        narrative = ""
        options = []
        response = {}

        # 流式输出
        if eng._stream_callback:
            try:
                token_gen = eng.player_agent.generate_narrative_stream(
                    eng.player_state, player_input,
                    world_state=eng.world_state.model_dump() if eng.world_state else None,
                    day=eng.world_state.current_day if eng.world_state else 1,
                    npc_states=eng.npc_states,
                    narrative_history=eng.narrative_history,
                    fixed_prompt=fixed_prompt,
                    max_context=eng._get_max_context(),
                )
                for token in token_gen:
                    if token:
                        narrative += token
                        eng._stream_callback(token)
                eng._stream_callback(None)
                # [v8修复] 如果LLM返回了JSON格式，提取narrative字段
                narrative = self._extract_narrative_from_json(narrative)
                # [v9] 流式输出也应用灰色文本清理
                if narrative and eng.player_agent:
                    narrative = eng.player_agent._clean_narrative(narrative)
            except Exception as e:
                logger.warning("Stream narrative failed, falling back: %s", e)
                narrative = ""

        if narrative:
            # 流式成功，生成选项
            options_prompt = (
                f"根据以下叙事，生成3个玩家可选行动选项"
                f"（JSON格式，包含options数组，每个选项有id(A/B/C)、text、type、risk字段）："
                f"\n\n{narrative[:800]}"
            )
            try:
                opts_response = eng.llm.chat_json(options_prompt, temperature=0.5, max_tokens=0)
                options = opts_response.get("options", [])
            except Exception as e:
                logger.warning("Options generation failed, using fallback: %s", e)
            # [v8修复] 选项为空时使用fallback
            if not options:
                options = eng.player_agent._fallback_options(eng.player_state)
            response = {
                "narrative": narrative, "options": options,
                "status_changes": {}, "new_effects": [], "removed_effects": [],
                "relation_changes": {}, "identity_changes": {},
            }
        else:
            # 非流式生成
            response = eng.player_agent.generate_full_response(
                eng.player_state, player_input,
                world_state=eng.world_state.model_dump() if eng.world_state else None,
                day=eng.world_state.current_day if eng.world_state else 1,
                npc_names=npc_names,
                npc_states=eng.npc_states,
                narrative_history=eng.narrative_history,
                fixed_prompt=fixed_prompt,
                max_context=eng._get_max_context(),
                strip_gray=eng._get_strip_gray_narrative(),
            )
            narrative = response.get("narrative", "")
            options = response.get("options", [])

        return narrative, options, response

    def _handle_dice(self, response: dict) -> Optional[dict]:
        """处理骰子判定"""
        eng = self.engine
        if response.get("dice_check", {}).get("needed"):
            return eng.player_agent.dice_roll(
                response["dice_check"].get("stat", "intelligence"),
                response["dice_check"].get("difficulty", 10),
                eng.player_state,
            )
        return None

    def _handle_time_perception(self, narrative: str, player_input: str) -> tuple:
        """处理叙事时间感知"""
        eng = self.engine
        time_skip_result = None
        year_evolution_events = []

        if eng.timekeeper and eng.world_state and narrative:
            # [v9] 获取世界类型，传递给timekeeper
            world_type = "custom"
            if eng.world_def:
                world_type = eng.world_def.get("world_type", "custom")
            elif hasattr(eng.world_state, 'world_type'):
                world_type = eng.world_state.world_type

            time_skip_result = eng.timekeeper.parse_and_accumulate(
                text=narrative,
                player_input=player_input,
                current_game_day=eng.world_state.current_day,
                world_type=world_type,
            )
            if time_skip_result.get("days_advanced", 0) > 0:
                days_adv = time_skip_result["days_advanced"]
                logger.info("时间感知: 检测到%d天跳跃", days_adv)
                for _ in range(days_adv):
                    eng.age_system.advance_time(eng.world_state, hours=6)
                    if eng.world_state.current_day % 365 == 0:
                        eng.timekeeper.mark_year_evolved()
                        if eng.npc_life_evolution and eng.npc_states:
                            locs = list(eng.world_state.locations.keys()) if eng.world_state.locations else []
                            yr_events = eng.npc_life_evolution.evolve_year(
                                eng.npc_states, eng.world_state, locs
                            )
                            if yr_events:
                                year_evolution_events.extend(yr_events)
                eng._last_year_evolved = eng.world_state.current_day

        return time_skip_result, year_evolution_events

    def _handle_butterfly(self, player_input: str, narrative: str,
                          response: dict) -> tuple:
        """处理蝴蝶效应"""
        eng = self.engine
        impact = eng.butterfly.evaluate_impact(
            eng.player_state, player_input, eng.world_state
        )
        eng.player_impacts_today.append(
            f"行为: {player_input[:50]} -> 影响: {impact.get('description', '')[:100]}"
        )

        # 身份变更日志
        for entry in response.get("_identity_log", []):
            eng.player_impacts_today.append(entry)

        eng.butterfly.record_action(
            eng.player_state, player_input, narrative,
            eng.world_state.current_day if eng.world_state else 0
        )

        consequence = eng.butterfly.generate_consequence(impact, eng.world_state)
        world_event = None
        if consequence:
            eng.world_agent.update_world_state(eng.world_state, consequence)
            world_event = consequence.model_dump()
            eng.world_changes_today.append(consequence.description[:100])

        return impact, world_event

    def _handle_death(self, is_suicide: bool) -> tuple:
        """处理死亡检测"""
        eng = self.engine
        death = None
        suicide_confirm = None

        if eng.death_system and eng.player_state:
            if is_suicide:
                suicide_confirm = {
                    "type": "suicide_confirm",
                    "message": "你确认要结束自己的生命吗？未存档的进度将全部丢失。",
                    "cause": "自尽",
                }
            else:
                death = eng.death_system.check_death(eng.player_state, eng.world_state)
                if death and eng.memoir:
                    eng.memoir.record_death(
                        eng.player_state, death["cause"],
                        eng.world_state.current_day if eng.world_state else 0,
                        eng.world_state
                    )

        return death, suicide_confirm

    def _store_to_rag(self, narrative: str, player_input: str):
        """存入向量库"""
        eng = self.engine
        if narrative and eng.memory:
            day = eng.world_state.current_day if eng.world_state else 0
            eng.memory.add_narrative(narrative, day, player_input)
            if any(kw in narrative for kw in FORESHADOW_KEYWORDS):
                eng.memory.add_foreshadow(narrative, day, importance="high")

    def _build_graph_rag(self, narrative: str):
        """构建知识图谱"""
        eng = self.engine
        if eng.graph_rag and narrative:
            try:
                eng.graph_rag.build_from_narrative(narrative)
            except Exception as e:
                logger.warning("GraphRAG build failed: %s", e)

    def _run_identity_audit(self, narrative: str) -> list:
        """运行身份审计"""
        eng = self.engine
        audit_results = []
        eng.turns_since_audit += 1

        if (eng.turns_since_audit >= eng.audit_interval and narrative
                and eng.npc_states and eng.player_agent):
            eng.turns_since_audit = 0
            try:
                discrepancies = eng.player_agent.audit_identity_consistency(
                    narrative, eng.npc_states,
                    day=eng.world_state.current_day if eng.world_state else 0
                )
                for d in discrepancies:
                    matched_npc = None
                    for nid, npc in eng.npc_states.items():
                        if (d.get("npc_id") == nid or d.get("npc_id") == npc.name
                                or d.get("npc_id") in nid or nid in d.get("npc_id", "")):
                            matched_npc = npc
                            break
                    if matched_npc and d.get("is_legitimate_change") and d.get("suggested_fix"):
                        old_role = matched_npc.role
                        new_role = d["suggested_fix"]
                        if new_role != old_role:
                            matched_npc.record_role_change(
                                new_role, d.get("reason", "剧情演变"),
                                eng.world_state.current_day if eng.world_state else 0
                            )
                            audit_results.append(
                                f"🔍审计补标记: {matched_npc.name} {old_role}→{new_role}"
                            )
                            if eng.lorebook:
                                eng.lorebook.update_npc_entry(
                                    matched_npc.name, matched_npc.get_identity_summary()
                                )
            except Exception as e:
                logger.warning("Identity audit failed: %s", e)

        return audit_results

    def _handle_experience(self, player_input: str, narrative: str) -> Optional[dict]:
        """处理经验/等级"""
        eng = self.engine
        if not (eng.level_system and eng.level_system.system_type != "none"):
            return None

        action_type = eng._classify_action_type(player_input, narrative or "")
        exp_amount = eng.level_system.calc_exp_for_action(action_type)
        exp_result = eng.level_system.add_experience(exp_amount)

        if exp_result.get("leveled_up") and eng.player_state:
            eng.level_system.apply_level_bonuses(eng.player_state)

        near_hint = eng.level_system.get_near_level_up_hint()
        if near_hint and not exp_result.get("leveled_up"):
            exp_result["near_level_hint"] = near_hint

        # 过滤境界类标签
        if eng.player_state:
            level_names = eng.level_system.get_all_level_names()
            level_keywords = {
                "martial": ["凡人", "炼体", "内息", "先天", "宗师", "大宗师", "武圣", "武道"],
                "cultivation": ["凡人", "炼气", "筑基", "金丹", "元婴", "化神", "渡劫", "大乘"],
                "magic": ["学徒", "法师", "元素师", "魔导", "法圣", "魔法", "魔力"],
                "none": [],
            }
            keywords = level_keywords.get(eng.level_system.system_type, [])
            eng.player_state.tags = [
                t for t in eng.player_state.tags
                if t not in level_names and not any(kw in t for kw in keywords)
            ]

        return exp_result

    def _extract_narrative_from_json(self, text: str) -> str:
        """
        [v8修复] 如果LLM返回了JSON格式（如 {"narrative": "..."}），
        提取narrative字段的纯文本。如果不是JSON则原样返回。
        """
        if not text:
            return text
        stripped = text.strip()
        # 检测是否是JSON格式
        if stripped.startswith('{') and stripped.endswith('}'):
            try:
                import json
                data = json.loads(stripped)
                if isinstance(data, dict) and "narrative" in data:
                    return data["narrative"]
                # 如果有其他文本字段也尝试提取
                for key in ["text", "content", "story", "description"]:
                    if key in data and isinstance(data[key], str):
                        return data[key]
            except (json.JSONDecodeError, KeyError):
                pass
        # 尝试从混合文本中提取JSON块
        json_start = stripped.find('{"narrative"')
        if json_start >= 0:
            try:
                # 找到匹配的闭合括号
                depth = 0
                for i in range(json_start, len(stripped)):
                    if stripped[i] == '{':
                        depth += 1
                    elif stripped[i] == '}':
                        depth -= 1
                        if depth == 0:
                            json_str = stripped[json_start:i+1]
                            data = json.loads(json_str)
                            if "narrative" in data:
                                return data["narrative"]
                            break
            except (json.JSONDecodeError, KeyError):
                pass
        return text

    def _record_history(self, narrative: str, player_input: str,
                        world_event: Optional[dict]):
        """记录叙事历史"""
        eng = self.engine
        if narrative:
            eng.narrative_history.append({
                "type": "narrative",
                "day": eng.world_state.current_day if eng.world_state else 0,
                "time": eng.world_state.current_time if eng.world_state else "",
                "text": narrative,
                "player_input": player_input,
            })
        if world_event:
            eng.narrative_history.append({
                "type": "event",
                "day": eng.world_state.current_day if eng.world_state else 0,
                "time": eng.world_state.current_time if eng.world_state else "",
                "text": world_event.get("narrative", ""),
                "event_type": world_event.get("event_type", ""),
            })
        # [v10.1] 实时检查叙事历史长度，超过阈值立即触发压缩
        if len(eng.narrative_history) > eng.MAX_NARRATIVE_HISTORY and eng.memory_curator:
            try:
                summary_result = eng.memory_curator.summarize_history(
                    eng.narrative_history,
                    current_turn=eng.meta.turn_count if eng.meta else 0,
                    current_day=eng.world_state.current_day if eng.world_state else 1,
                )
                if summary_result.get("status") == "success":
                    eng.narrative_history = (
                        summary_result.get("replacement", [])
                        + summary_result.get("remaining", [])
                    )
                    eng._narrative_compressed = True
                    logger.info("Real-time narrative compression triggered: %d entries",
                                len(eng.narrative_history))
            except Exception as e:
                logger.warning("Real-time narrative compression failed: %s", e)
