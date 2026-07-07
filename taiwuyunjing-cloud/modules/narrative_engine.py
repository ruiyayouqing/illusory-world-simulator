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
from .prompt.narrative_prompts import (
    DAILY_CHAPTER_PROMPT, SCENE_NARRATIVE_PROMPT,
    DYNAMIC_OPTIONS_PROMPT, REACTION_NARRATIVE_PROMPT,
    MORNING_INTRO_PROMPT, DAILY_NOVEL_CHAPTER_PROMPT,
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

    def _compress_full_log(self, full_log: str, max_tokens: int = 3000) -> str:
        """
        [v10++] 压缩当日事件日志，避免过长日志挤占叙事 token 预算。
        优先使用 ContextEngine 的轻量压缩（保留首尾）；不可用时原样返回。
        注意：不压缩到过小，保留足够事件细节以维持叙事质量。
        """
        if not full_log:
            return ""
        current = estimate_tokens(full_log)
        if current <= max_tokens:
            return full_log
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
        prompt = REACTION_NARRATIVE_PROMPT.format(
            style_instruction=self._get_style_instruction(world_style),
            player_action=player_action,
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
                               world_style: str = "") -> str:
        relations_text = ", ".join([
            f"{k}(好感{v.favor})" for k, v in player.relations.items()
        ]) or "无"

        world_context = (
            f"{world_state.world_name}, 第{world_state.current_day}天, "
            f"{world_state.season}, {world_state.weather}, "
            f"危机等级{world_state.crisis_level}/10"
        )

        # [v10++] 压缩过长的当日事件日志，避免挤占叙事 token 预算
        full_log = self._compress_full_log(full_log, max_tokens=3000)

        prompt = DAILY_NOVEL_CHAPTER_PROMPT.format(
            style_instruction=self._get_style_instruction(world_style),
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
        return self.llm.chat(prompt, temperature=0.9, max_tokens=1500)

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
