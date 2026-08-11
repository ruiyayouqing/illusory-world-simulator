"""
[v9] 叙事引擎 — 集成叙事风格管理器
所有prompt统一通过 {style_instruction} 注入当前风格指令。

[v10++] 集成上下文引擎：当 full_log 过长时，使用 ContextEngine 轻量压缩，
避免叙事相关上下文被过度截断而影响质量。
"""
from __future__ import annotations
import logging
from typing import Optional
from .schemas import PlayerState, WorldState, NPCState
from .llm.base_llm import BaseLLM
from .narrative_style import NarrativeStyleManager
from .context_budget import estimate_tokens
from .prompt_utils import sanitize_player_input  # [v1.4 P2-10] Prompt injection 防护
from .prompt.narrative_prompts import (
    DAILY_CHAPTER_PROMPT, SCENE_NARRATIVE_PROMPT,
    DYNAMIC_OPTIONS_PROMPT, REACTION_NARRATIVE_PROMPT,
    MORNING_INTRO_PROMPT, DAILY_NOVEL_CHAPTER_PROMPT,
    AUTORUN_NOVEL_CHAPTER_PROMPT,
    WORLD_EVOLUTION_SUMMARY_PROMPT,
)

logger = logging.getLogger("chronoverse.narrative_engine")


class NarrativeEngine:
    def __init__(self, llm: BaseLLM,
                 style_manager: Optional[NarrativeStyleManager] = None,
                 context_engine=None):
        self.llm = llm
        self.style_manager = style_manager
        # [v10++] 上下文引擎（可选）：用于压缩过长的 full_log
        self.context_engine = context_engine

    def _get_style_instruction(self, world_style: str = "") -> str:
        """获取当前风格指令，如果无style_manager则使用默认章回体"""
        if self.style_manager:
            return self.style_manager.get_style_instruction(world_style)
        return "【写作风格：章回体】\n以章回体小说风格撰写，语言半文半白。"

    @staticmethod
    def _location_display(player: PlayerState, world_state: WorldState) -> str:
        """[Bug] 获取地点显示名（如"汴京城"），而非 location code（如"bianjing"）"""
        loc_code = player.location if player else "此处"
        if world_state and hasattr(world_state, 'locations') and loc_code in world_state.locations:
            loc_obj = world_state.locations[loc_code]
            if isinstance(loc_obj, dict):
                return loc_obj.get('location_name') or loc_obj.get('name') or loc_code
            elif hasattr(loc_obj, 'location_name'):
                return loc_obj.location_name or loc_code
            elif hasattr(loc_obj, 'name'):
                return loc_obj.name or loc_code
        return loc_code

    def _compress_full_log(self, full_log: str, max_tokens: int = 50000) -> str:
        """
        [2026-08-09] 章节上文压缩：尾部完整保留 + 头部 LLM 摘要（前情提要）。

        背景：原实现用 ContextEngine 硬截断（保留首尾、丢弃中段），
        导致章节生成看不到中间的关键剧情（如重要事件/伏笔），与上文脱节。
        现方案：预算内直接使用；超预算时——
          - 尾部（约 70% 预算）保留完整原文（最近细节不丢）
          - 头部（更早的剧情）用一次 LLM 调用压成 600 字左右"前情提要"（脉络不丢）
        摘要失败时回退到原有硬截断逻辑，保证不阻塞。
        """
        if not full_log:
            return ""
        current = estimate_tokens(full_log)
        if current <= max_tokens:
            return full_log
        # 尾部保留预算（token），头部交给摘要
        tail_tokens = int(max_tokens * 0.7)
        tail_chars = max(200, int(tail_tokens / 1.5))  # 1 中文字 ≈ 1.5 token
        head_text = full_log[:-tail_chars]
        tail_text = full_log[-tail_chars:]
        try:
            summary = self._summarize_log(head_text)
            if summary:
                logger.info(
                    "full_log 摘要压缩: %d -> 前情提要(%d字) + 尾部完整(%d字)",
                    current, len(summary), len(tail_text),
                )
                return f"【前情提要（早期剧情摘要，已压缩）】\n{summary}\n\n【近期完整记录】\n{tail_text}"
        except Exception as e:
            logger.warning("full_log 摘要失败，回退硬截断: %s", e)
        # 回退：ContextEngine 硬截断（保留首尾）
        if self.context_engine:
            try:
                compressed = self.context_engine.compress_text(full_log, max_tokens)
                logger.info(
                    "full_log compressed by ContextEngine: %d -> %d tokens",
                    current, estimate_tokens(compressed),
                )
                return compressed
            except Exception as e:
                logger.warning("ContextEngine compress failed, keep original: %s", e)
        return full_log

    def _summarize_log(self, text: str) -> str:
        """把早期剧情压缩成前情提要（剧情纲要），供章节创作参考。"""
        if not text:
            return ""
        # 摘要输入限制在 4 万字内（约 6 万 token），避免极端超长输入
        clip = text[-40000:]
        prompt = (
            "请将以下游戏剧情记录压缩成一份【前情提要】（剧情纲要），供后续小说章节创作参考。\n"
            "\n"
            "【要求】\n"
            "1. 1800-2200字，按时间线概括关键事件、人物关系变化、重要伏笔\n"
            "2. 保留具体人名、地名、事件因果链\n"
            "3. 主要支线和配角也要提及，不能只写主线\n"
            "4. 只陈述事实脉络，不要文学化描写、不要抒情\n"
            "5. 结尾用一句话点明当前剧情进行到何处\n"
            "\n"
            f"【早期剧情记录】\n{clip}\n"
            "\n"
            "直接输出前情提要："
        )
        return self.llm.chat(prompt, temperature=0.3, max_tokens=3000)

    def generate_daily_chapter(self, event_log: str, player: PlayerState,
                               world_state: WorldState, day: int,
                               world_style: str = "") -> dict:
        relations_text = ", ".join([
            f"{k}(好感{v.favor})" for k, v in player.relations.items()
        ]) or "无"

        prompt = DAILY_CHAPTER_PROMPT.format(
            style_instruction=self._get_style_instruction(world_style),
            event_log=event_log,
            player_name=player.name,
            player_age=player.age,
            player_position=player.social.position,
            location=self._location_display(player, world_state),
            tags=", ".join(player.tags),
            status_effects=", ".join(player.status_effects) if player.status_effects else "正常",
            relations=relations_text,
            world_context=f"{world_state.world_name}, 第{world_state.current_day}天, {world_state.season}, {world_state.weather}",
        )
        content = self.llm.chat(prompt, temperature=0.9, max_tokens=1024)
        return {
            "chapter": day,
            "title": f"第{day}回",
            "content": content,
            "day_range": [day, day],
        }

    def generate_scene_narrative(self, location: str, time: str, weather: str,
                                 actors: list[str], event_or_action: str,
                                 player: PlayerState,
                                 world_style: str = "") -> str:
        actors_text = ", ".join(actors) if actors else "无"
        player_state = (
            f"{player.name}, {player.age}岁, {player.social.position}\n"
            f"状态: {', '.join(player.status_effects) if player.status_effects else '正常'}\n"
            f"标签: {', '.join(player.tags)}"
        )
        prompt = SCENE_NARRATIVE_PROMPT.format(
            style_instruction=self._get_style_instruction(world_style),
            location=location,
            time=time,
            weather=weather,
            actors=actors_text,
            event_or_action=event_or_action,
            player_state=player_state,
        )
        return self.llm.chat(prompt, temperature=0.85)

    def generate_dynamic_options(self, scene_description: str, player: PlayerState,
                                 relations: dict = None,
                                 world_style: str = "") -> list[dict]:
        relations_text = ""
        if relations:
            relations_text = ", ".join([
                f"{k}(好感{v.favor})" for k, v in relations.items()
            ]) or "无"
        elif player.relations:
            relations_text = ", ".join([
                f"{k}(好感{v.favor})" for k, v in player.relations.items()
            ]) or "无"

        prompt = DYNAMIC_OPTIONS_PROMPT.format(
            style_instruction=self._get_style_instruction(world_style),
            scene_description=scene_description,
            player_name=player.name,
            tags=", ".join(player.tags),
            strength=player.stats.strength,
            agility=player.stats.agility,
            intelligence=player.stats.intelligence,
            luck=player.stats.luck,
            health=player.stats.health,
            max_health=player.stats.max_health,
            energy=player.stats.energy,
            max_energy=player.stats.max_energy,
            gold=player.social.gold,
            status_effects=", ".join(player.status_effects) if player.status_effects else "正常",
            relations=relations_text,
        )
        response = self.llm.chat_json(prompt, temperature=0.8)
        return response.get("options", self._default_options())

    def generate_reaction(self, player_action: str, action_result: str,
                          location: str, time: str,
                          world_style: str = "") -> str:
        # [v1.4 P2-10] Prompt injection 防护
        safe_action = sanitize_player_input(player_action)
        prompt = REACTION_NARRATIVE_PROMPT.format(
            style_instruction=self._get_style_instruction(world_style),
            player_action=safe_action,
            action_result=action_result,
            location=location,
            time=time,
        )
        return self.llm.chat(prompt, temperature=0.8)

    def generate_morning_intro(self, player: PlayerState, world_state: WorldState,
                               yesterday_summary: str = "",
                               world_style: str = "") -> str:
        prompt = MORNING_INTRO_PROMPT.format(
            style_instruction=self._get_style_instruction(world_style),
            day=world_state.current_day,
            season=world_state.season,
            weather=world_state.weather,
            player_name=player.name,
            player_age=player.age,
            location=self._location_display(player, world_state),
            status_effects=", ".join(player.status_effects) if player.status_effects else "正常",
            current_goal=player.current_goal,
            yesterday_summary=yesterday_summary or "昨日平安无事。",
        )
        return self.llm.chat(prompt, temperature=0.85)

    def generate_novel_chapter(self, player: PlayerState, world_state: WorldState,
                               full_log: str, age_info: str = "",
                               economy_info: str = "", butterfly_info: str = "",
                               world_style: str = "",
                               world_intro: str = "", npc_context: str = "",
                               max_tokens: int = 1500,
                               log_budget: int = 150000,
                               days_span: int = 1) -> str:
        relations_text = ", ".join([
            f"{k}(好感{v.favor})" for k, v in player.relations.items()
        ]) or "无"

        world_context = (
            f"{world_state.world_name}, 第{world_state.current_day}天, "
            f"{world_state.season}, {world_state.weather}, "
            f"危机等级{world_state.crisis_level}/10"
        )

        # [v10++] 压缩过长的当日事件日志，避免挤占叙事 token 预算
        # [v1.2] auto-run 多日汇总时放大日志预算，保留更多事件细节
        full_log = self._compress_full_log(full_log, max_tokens=log_budget)

        # [v1.2] 多日汇总时使用跨日章节 prompt，避免"第X天...第Y天..."流水账
        is_multi_day = days_span > 1
        if is_multi_day:
            prompt_template = AUTORUN_NOVEL_CHAPTER_PROMPT
            prompt = prompt_template.format(
                style_instruction=self._get_style_instruction(world_style),
                world_intro=world_intro or "（无）",
                npc_context=npc_context or "（无）",
                full_log=full_log,
                player_name=player.name,
                player_age=player.age,
                player_position=player.social.position,
                location=self._location_display(player, world_state),
                tags=", ".join(player.tags),
                strength=player.stats.strength,
                agility=player.stats.agility,
                intelligence=player.stats.intelligence,
                luck=player.stats.luck,
                health=player.stats.health,
                max_health=player.stats.max_health,
                energy=player.stats.energy,
                max_energy=player.stats.max_energy,
                gold=player.social.gold,
                reputation=player.social.reputation,
                status_effects=", ".join(player.status_effects) if player.status_effects else "正常",
                relations=relations_text,
                world_context=world_context,
                age_info=age_info or "无年龄变化",
                economy_info=economy_info or "无经济变化",
                butterfly_info=butterfly_info or "你的行为尚未在世界上留下深刻印记。",
                days_span=days_span,
            )
        else:
            prompt = DAILY_NOVEL_CHAPTER_PROMPT.format(
                style_instruction=self._get_style_instruction(world_style),
                world_intro=world_intro or "（无）",
                npc_context=npc_context or "（无）",
                full_log=full_log,
                player_name=player.name,
                player_age=player.age,
                player_position=player.social.position,
                location=self._location_display(player, world_state),
                tags=", ".join(player.tags),
                strength=player.stats.strength,
                agility=player.stats.agility,
                intelligence=player.stats.intelligence,
                luck=player.stats.luck,
                health=player.stats.health,
                max_health=player.stats.max_health,
                energy=player.stats.energy,
                max_energy=player.stats.max_energy,
                gold=player.social.gold,
                reputation=player.social.reputation,
                status_effects=", ".join(player.status_effects) if player.status_effects else "正常",
                relations=relations_text,
                world_context=world_context,
                age_info=age_info or "无年龄变化",
                economy_info=economy_info or "无经济变化",
                butterfly_info=butterfly_info or "你的行为尚未在世界上留下深刻印记。",
            )
        return self.llm.chat(prompt, temperature=0.9, max_tokens=max_tokens)

    def generate_world_evolution(self, all_events: str, player_impacts: str,
                                 world_changes: str,
                                 world_style: str = "") -> str:
        prompt = WORLD_EVOLUTION_SUMMARY_PROMPT.format(
            style_instruction=self._get_style_instruction(world_style),
            all_events=all_events,
            player_impacts=player_impacts,
            world_changes=world_changes,
        )
        return self.llm.chat(prompt, temperature=0.8)

    def _default_options(self) -> list[dict]:
        return [
            {"id": "A", "text": "四处看看", "type": "search", "risk": "low",
             "needs_dice": False, "hint": "观察周围环境"},
            {"id": "B", "text": "找个地方休息", "type": "rest", "risk": "low",
             "needs_dice": False, "hint": "恢复体力"},
            {"id": "C", "text": "主动出击", "type": "action", "risk": "high",
             "needs_dice": True, "dice_stat": "strength", "dice_difficulty": 12,
             "hint": "需要力量判定"},
        ]
