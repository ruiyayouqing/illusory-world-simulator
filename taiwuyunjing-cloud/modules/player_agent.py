from __future__ import annotations
import random
import json
import logging
from .agent_base import BaseAgent
from .schemas import (
    PlayerState, Stats, Social, Inventory, InventoryItem,
    RelationEntry, PlayerMemory, NPCState,
)
from .llm.base_llm import BaseLLM
from .llm.router import TASK_DIALOGUE, TASK_CLASSIFY, TASK_SIMPLE, TASK_NARRATIVE
from .db.chroma_db import MemoryStore
from .lorebook import Lorebook
from .prompt.player_prompts import (
    SYSTEM_PROMPT, EXTRACT_INTENT_PROMPT,
    DAILY_SUMMARY_PROMPT, OPTIONS_PROMPT,
)
from .prompt_utils import (
    build_npc_context, build_world_context, build_player_context,
    build_history_context, resolve_location_name,  # [Bug] location code → display name
)
from .context_budget import (
    build_system_context_with_budget, estimate_tokens, detect_scene,
)
from .context_engine import ContextEngine

logger = logging.getLogger("chronoverse.player_agent")


class PlayerAgent(BaseAgent):
    """玩家 Agent：继承 BaseAgent，集成动态上下文预算和双过程记忆"""

    def __init__(self, llm: BaseLLM, memory: MemoryStore, lorebook: Lorebook = None,
                 context_engine: ContextEngine = None):
        super().__init__(llm, memory=memory, lorebook=lorebook)
        # [v10++] 上下文引擎：注意力预算 + 提示压缩 + Prompt Caching
        # 可通过构造函数或 set_context_engine 注入；为 None 时回退到旧逻辑
        self.context_engine: ContextEngine | None = context_engine
        # [v10++] 角色动态状态管理器引用（CHIRON 式），可选
        # 由 GameEngine._init_services 注入。
        # 注意：主流程中动态状态已由 TurnProcessorV2 通过 fixed_prompt 注入，
        # 此引用供 PlayerAgent 被直接调用时按需使用。
        self.character_state_manager = None
        # [v10.6+] RAG检索缓存：避免重复嵌入
        self._rag_cache = {}
        self._rag_cache_ttl = 300  # 5分钟
        # [v11] 增大缓存容量，典型游戏中每回合1次嵌入，300回合不淘汰
        self._rag_cache_max = 500  # 原100
        # [v11] 实体线索索引缓存：避免每回合重建
        self._entity_index_cache: dict[str, list[str]] | None = None
        self._entity_index_cache_hash: int = 0
        # [v11] LLM查询扩展缓存（短TTL，避免重复调用）
        self._llm_expansion_cache: dict[str, tuple[float, dict | None]] = {}
        self._llm_expansion_cache_ttl = 60  # 60秒
        # [v11] 伏笔生命周期管理器引用（由 GameEngine 注入）
        self.foreshadow_lifecycle = None

    def _get_rag_cache(self, prompt: str) -> str | None:
        import hashlib
        import time
        key = hashlib.md5(prompt.strip()[:500].encode()).hexdigest()
        if key in self._rag_cache:
            ts, val = self._rag_cache[key]
            if time.time() - ts < self._rag_cache_ttl:
                return val
            del self._rag_cache[key]
        return None

    def _set_rag_cache(self, prompt: str, val: str):
        import hashlib
        import time
        key = hashlib.md5(prompt.strip()[:500].encode()).hexdigest()
        self._rag_cache[key] = (time.time(), val)
        if len(self._rag_cache) > self._rag_cache_max:  # [v11] 使用配置的大小
            oldest = min(self._rag_cache, key=lambda k: self._rag_cache[k][0])
            del self._rag_cache[oldest]

    def set_context_engine(self, context_engine: ContextEngine):
        """注入上下文引擎（由 game_engine 在服务初始化后调用）。"""
        self.context_engine = context_engine
        logger.debug("ContextEngine injected into PlayerAgent")

    def _build_context_with_engine(
        self,
        system_prompt: str,
        world_text: str,
        npc_text: str,
        identity_text: str,
        lorebook_text: str,
        rag_text: str,
        history_text: str,
        player_text: str,
        fixed_prompt: str,
        state: PlayerState,
        max_context: int,
    ) -> str:
        """
        [v10++] 使用 ContextEngine 构建优化后的上下文。
        按优先级分配注意力预算，压缩低优先级层，并记录缓存前缀命中率。
        失败时抛出异常，由调用方回退到 build_system_context_with_budget。
        """
        engine = self.context_engine
        # 临时调整预算上限，与本次调用期望一致
        # [P0-2] 同步调整 max_output，避免 max_context - max_output 出现负数
        engine.max_context = max_context
        # max_output 不应超过 max_context 的一半，且至少保留一半给上下文
        engine.max_output = min(engine.max_output, max_context // 2)

        # 角色上下文 = NPC 设定 + 长期身份记忆
        character_context = npc_text
        if identity_text:
            character_context = (character_context + "\n" + identity_text) if character_context else identity_text

        # 长期摘要作为 summary 层（低优先级，可压缩）
        summary_context = ""
        if state.memory and getattr(state.memory, "long_term_summary", ""):
            summary_context = state.memory.long_term_summary

        # [v11] P1a: 获取活跃伏笔追踪文本（不可压缩，确保AI始终知道待回收伏笔）
        active_foreshadows_text = ""
        if self.foreshadow_lifecycle:
            try:
                active_foreshadows_text = self.foreshadow_lifecycle.get_hooks_for_prompt(max_hooks=5)
            except Exception as e:
                logger.debug("Failed to get active foreshadows: %s", e)

        # 记录可缓存前缀（系统提示 + 世界观 + 角色卡）命中率，供观测
        engine.cache.get_cacheable_prefix(system_prompt, world_text, character_context)

        system_context = engine.build_context(
            system_prompt="",  # formatted_system_prompt 作为独立 system 消息
            world_context=world_text,
            character_context=character_context,
            foreshadow_context=lorebook_text,
            summary_context=summary_context,
            rag_context=rag_text,
            recent_history=history_text,
            player_input=player_text,
            active_foreshadows=active_foreshadows_text,
        )

        # fixed_prompt 为高优先级固定指令，前置注入（不可压缩）
        if fixed_prompt:
            system_context = fixed_prompt + "\n\n" + system_context

        logger.info(
            "ContextEngine used: cache_stats=%s",
            engine.cache.get_stats(),
        )
        return system_context

    # ── BaseAgent 抽象接口实现 ────────────────────────────

    def plan_next_action(self, state: PlayerState, world_state=None,
                         context: dict = None) -> dict:
        """玩家不需要自动规划，返回空"""
        return {"planned": False, "actions": []}

    def execute_action(self, action: dict, state: PlayerState,
                       world_state=None) -> dict:
        """玩家行动由 LLM 叙事驱动，此处为回退"""
        return {"action": action.get("type", "idle"),
                "detail": action.get("detail", "")}

    def extract_intent(self, player_input: str, state: PlayerState, world_state=None) -> dict:  # [Bug] 增加 world_state 参数
        prompt = EXTRACT_INTENT_PROMPT.format(
            player_input=player_input,
            location=resolve_location_name(state.location, world_state),  # [Bug] location code → display name
            tags=", ".join(state.tags),
            status_effects=", ".join(state.status_effects) if state.status_effects else "无",
        )
        # [v10] 优先使用结构化输出（意图分类 schema），失败回退到 chat_json
        if hasattr(self.llm, "chat_structured"):
            try:
                return self.llm.chat_structured(prompt, "intent", temperature=0.3, task_type=TASK_CLASSIFY)
            except Exception as e:
                logger.warning("Structured intent extraction failed, fallback: %s", e)
        return self.llm.chat_json(prompt, temperature=0.3, task_type=TASK_CLASSIFY)

    def dice_roll(self, stat: str, difficulty: int, state: PlayerState) -> dict:
        stat_value = getattr(state.stats, stat, 5)
        luck_bonus = state.stats.luck // 3
        roll = random.randint(1, 20)
        total = stat_value + luck_bonus + roll
        success = total >= difficulty
        return {
            "stat": stat,
            "stat_value": stat_value,
            "luck_bonus": luck_bonus,
            "roll": roll,
            "total": total,
            "difficulty": difficulty,
            "success": success,
        }

    def apply_status_changes(self, state: PlayerState, changes: dict) -> list[str]:
        log = []
        for key, delta in changes.items():
            try:
                delta = int(delta)
            except (TypeError, ValueError):
                continue
            if key == "health":
                old = state.stats.health
                state.stats.health = max(0, min(state.stats.max_health, old + delta))
                log.append(f"生命: {old} -> {state.stats.health}")
            elif key == "energy":
                old = state.stats.energy
                state.stats.energy = max(0, min(state.stats.max_energy, old + delta))
                log.append(f"体力: {old} -> {state.stats.energy}")
            elif key == "strength":
                old = state.stats.strength
                state.stats.strength = max(1, old + delta)
                log.append(f"力量: {old} -> {state.stats.strength}")
            elif key == "agility":
                old = state.stats.agility
                state.stats.agility = max(1, old + delta)
                log.append(f"敏捷: {old} -> {state.stats.agility}")
            elif key == "intelligence":
                old = state.stats.intelligence
                state.stats.intelligence = max(1, old + delta)
                log.append(f"智力: {old} -> {state.stats.intelligence}")
            elif key == "luck":
                old = state.stats.luck
                state.stats.luck = max(1, old + delta)
                log.append(f"幸运: {old} -> {state.stats.luck}")
            elif key == "magic":
                old = getattr(state.stats, "magic", 0)
                state.stats.magic = max(0, old + delta)
                log.append(f"法力: {old} -> {state.stats.magic}")
            elif key == "gold":
                old = state.social.gold
                state.social.gold = max(0, old + delta)
                log.append(f"金币: {old} -> {state.social.gold}")
            elif key == "reputation":
                old = state.social.reputation
                state.social.reputation = max(0, old + delta)
                log.append(f"声望: {old} -> {state.social.reputation}")
        return log

    def apply_tags(self, state: PlayerState, new_tags: list, removed_tags: list):
        for tag in new_tags:
            if tag not in state.tags and len(state.tags) < 30:
                state.tags.append(tag)
        for tag in removed_tags:
            if tag in state.tags:
                state.tags.remove(tag)
        state.tags = self._consolidate_tags(state.tags)

    def _consolidate_tags(self, tags: list[str]) -> list[str]:
        merge_map = {
            "知晓穿越线索": "穿越者", "知晓穿越基本": "穿越者",
            "掌握基础火焰术": "火系基础", "火焰术掌握": "火系基础",
            "炼精化气初境": "炼气初期", "炼精化气精义领悟": "炼气初期",
            "灵气循环熟练": "炼气初期", "引气入体成功": "炼气初期",
            "引气入体状态": "炼气初期", "灵气巩固状态": "炼气初期",
            "观想初窥": "观想入门", "知晓观想法奥义": "观想入门",
            "传统观想理解": "观想入门", "观想法认知深化": "观想入门",
            "凌霄佩已认主": "凌霄佩主", "凌霄佩主": "凌霄佩主",
            "知晓凌霄佩详细功能": "凌霄佩主", "玉佩功能意识深化": "凌霄佩主",
            "知晓凌霄仙子道统": "凌霄传承", "知晓修仙基础": "修仙入门",
            "知晓修行等级体系": "修仙入门", "知晓修行基本功法": "修仙入门",
            "知晓引气入体步骤": "修仙入门", "炼精化气认知深化": "修仙入门",
            "现代知识展示": "穿越者", "科学修仙探索": "穿越者",
            "青萝指导": "青萝关系", "青萝侍从": "青萝关系",
            "青萝思维启发": "青萝关系", "青萝知识启发": "青萝关系",
            "清风寺线索关注": "清风寺探索", "清风寺准备": "清风寺探索",
            "清风寺地图详备": "清风寺探索", "清风寺抵达": "清风寺探索",
            "地图绘制完成": "探索技能", "灵气地图": "探索技能",
            "瞬移体验": "特殊能力", "修仙实践者": "修仙实践",
        }
        consolidated = []
        seen = set()
        for tag in tags:
            merged = merge_map.get(tag, tag)
            if merged not in seen:
                seen.add(merged)
                consolidated.append(merged)
        return consolidated[:25]

    def apply_effects(self, state: PlayerState, new_effects: list, removed_effects: list):
        for eff in new_effects:
            if eff not in state.status_effects and len(state.status_effects) < 15:
                state.status_effects.append(eff)
        for eff in removed_effects:
            if eff in state.status_effects:
                state.status_effects.remove(eff)
        state.status_effects = self._consolidate_effects(state.status_effects)

    def _consolidate_effects(self, effects: list[str]) -> list[str]:
        if len(effects) <= 10:
            return effects
        priority_keywords = ["中毒", "重伤", "诅咒", "封印", "濒死", "疯狂"]
        prioritized = [e for e in effects if any(k in e for k in priority_keywords)]
        others = [e for e in effects if e not in prioritized]
        max_others = 10 - len(prioritized)
        return prioritized + others[:max_others]

    def apply_relation_changes(self, state: PlayerState, changes,
                               npc_names: list[str] = None,
                               npc_states: dict = None,
                               day: int = 0):
        """[v10.5] 兼容 LLM 返回 list 格式（自动转换为 dict）。"""
        # [v10.5] 类型兼容：LLM 有时返回 list 而非 dict
        if isinstance(changes, list):
            changes = {c.get("npc_id", c.get("name", "")): c for c in changes if isinstance(c, dict)}
        if not isinstance(changes, dict):
            return
        name_map = {}
        if npc_names:
            for n in npc_names:
                name_map[n.lower()] = n
                name_map[n] = n
        for npc_id, change in changes.items():
            matched_name = npc_id
            if npc_id in name_map:
                matched_name = name_map[npc_id]
            elif npc_id.lower() in name_map:
                matched_name = name_map[npc_id.lower()]
            elif npc_names:
                for n in npc_names:
                    if npc_id in n or n in npc_id:
                        matched_name = n
                        break
            if matched_name == npc_id and npc_id.isascii():
                continue
            rt = change.get("relation_type", "")
            fv = change.get("favor", 0)
            try:
                fv = int(fv)
            except (TypeError, ValueError):
                continue
            if matched_name in state.relations:
                rel = state.relations[matched_name]
                rel.favor = max(0, min(100, rel.favor + fv))
                if rt:
                    rel.relation_type = rt
                if "description" in change:
                    rel.description = change["description"]
            else:
                state.relations[matched_name] = RelationEntry(
                    favor=max(0, min(100, 50 + fv)),
                    relation_type=rt or "陌生人",
                    description=change.get("description", ""),
                )
            if npc_states and matched_name in npc_states:
                npc = npc_states[matched_name]
                npc.relation_to_player.favor = state.relations[matched_name].favor
                if rt:
                    npc.relation_to_player.relation_type = rt
                    # 记录关系变更历史
                    npc.record_relation_change(rt, change.get("description", ""), day)

    def apply_identity_changes(self, identity_changes: dict, npc_states: dict,
                               day: int = 0) -> list[str]:
        """处理LLM返回的身份变更标记，更新NPC的role和relation"""
        log = []
        if not identity_changes or not npc_states:
            return log

        for npc_key, change in identity_changes.items():
            # 匹配NPC（支持中文名/ID模糊匹配）
            matched_npc = None
            for nid, npc in npc_states.items():
                if npc_key == nid or npc_key == npc.name or \
                   npc_key in nid or nid in npc_key or \
                   npc_key in npc.name or npc.name in npc_key:
                    matched_npc = npc
                    break
            if not matched_npc:
                continue

            old_role = matched_npc.role
            new_role = change.get("role", "")
            reason = change.get("reason", "剧情发展")

            if new_role and new_role != old_role:
                matched_npc.record_role_change(new_role, reason, day)
                log.append(f"🔀 {matched_npc.name}: {old_role or '(无)'} → {new_role} ({reason})")

                # 同步更新 tags
                if old_role and old_role in matched_npc.tags:
                    matched_npc.tags.remove(old_role)
                if new_role and new_role not in matched_npc.tags:
                    matched_npc.tags.insert(0, new_role)

                # 保持 tags 在合理数量
                if len(matched_npc.tags) > 10:
                    matched_npc.tags = matched_npc.tags[:10]

        return log

    def update_memory(self, state: PlayerState, event_text: str, day: int):
        """更新记忆，使用 BaseAgent 的共享实现，额外触发身份整合"""
        super().update_memory(state, event_text, day)
        # 定期触发身份整合（每10次记忆更新）
        self._memory_update_count = getattr(self, '_memory_update_count', 0) + 1
        if self.memory and self._memory_update_count % 10 == 0:
            self._try_consolidate_identity(state)

    def process_player_input(self, player_input: str, state: PlayerState,
                             world_state: dict = None, day: int = 1) -> dict:
        intent = self.extract_intent(player_input, state, world_state)  # [Bug] 传入 world_state 以解析 location code

        dice_result = None
        if intent.get("needs_dice"):
            dice_result = self.dice_roll(
                intent.get("dice_stat", "intelligence"),
                intent.get("dice_difficulty", 10),
                state,
            )

        state_summary = self._build_state_summary(state, world_state)
        options_prompt = OPTIONS_PROMPT.format(
            state_summary=state_summary,
            tags=", ".join(state.tags),
        )
        # [v10] 优先使用结构化输出（选项 schema），失败回退到 chat_json
        if hasattr(self.llm, "chat_structured"):
            try:
                options_response = self.llm.chat_structured(options_prompt, "options", temperature=0.8, task_type=TASK_DIALOGUE)
            except Exception as e:
                logger.warning("Structured options generation failed, fallback: %s", e)
                options_response = self.llm.chat_json(options_prompt, temperature=0.8, task_type=TASK_DIALOGUE)
        else:
            options_response = self.llm.chat_json(options_prompt, temperature=0.8, task_type=TASK_DIALOGUE)
        options = options_response.get("options", [])

        return {
            "intent": intent,
            "dice_result": dice_result,
            "options": options,
        }

    def generate_narrative_stream(self, state: PlayerState, player_input: str,
                                   world_state: dict = None, day: int = 1,
                                   npc_states: dict = None,
                                   narrative_history: list[dict] = None,
                                   fixed_prompt: str = "",
                                   max_context: int = 32768,
                                   scene_type=None,
                                   narrative_max_chars: int = 1000):
        """
        流式生成叙事文本。返回生成器，逐 token yield。
        与 generate_full_response 共享相同的上下文构建逻辑。

        [v10+] scene_type: 叙事场景类型，传入检索系统以动态调整 GraphRAG 权重。
        """
        npc_text = build_npc_context(npc_states, player_input, world_state) if npc_states else ""
        world_text = build_world_context(world_state) if world_state else ""
        player_text = build_player_context(state, world_state)
        history_text = build_history_context(player_input, narrative_history) if narrative_history else ""
        lorebook_text = self.lorebook.to_prompt(player_input) if self.lorebook else ""
        rag_text = self._build_rag_context(player_input, scene_type=scene_type,
                                            npc_states=npc_states, world_state=world_state,
                                            current_day=day)
        # 双过程记忆：注入身份上下文
        identity_text = self.memory.get_identity_context() if self.memory else ""
        # 动态场景检测
        scene = detect_scene(player_input, narrative_history)

        # [v10++] 优先使用 ContextEngine，失败回退到预算管理器
        stream_system_prompt = "你是一个小说作家。只输出纯文本叙事，禁止输出JSON或任何结构化格式。直接开始写故事内容。"
        # [v12] 时间跳跃标记：AI在叙事中时间有明显跳跃时，在末尾附加标记
        stream_system_prompt += (
            "\n\n【时间跳跃规则】"
            "\n如果叙事中时间有明显跳跃(如'过了三个月'、'转眼半年'、'数日后'等)，"
            "必须在叙事正文的最末尾单独一行附加时间标记，格式为："
            "\n<!--TIME_SKIP:天数-->"
            "\n其中天数为跳过的天数(如3个月=90天，半年=182天，一年=365天)。"
            "\n注意：标记必须在正文结束后单独一行，不要在正文中插入。"
            "\n如果时间没有跳跃，不要输出任何标记。"
        )
        # [Bug] 玩家身份锚点：防止流式生成时 LLM 幻觉改名
        stream_system_prompt += (
            f"\n\n【玩家身份锚点 - 最高优先级】\n"
            f"玩家姓名：{state.name}\n玩家身份：{state.social.position}\n"
            f"叙事中必须始终使用「{state.name}」称呼玩家，绝对禁止改名。"
        )
        if self.context_engine:
            try:
                system_context = self._build_context_with_engine(
                    system_prompt=stream_system_prompt,
                    world_text=world_text,
                    npc_text=npc_text,
                    identity_text=identity_text,
                    lorebook_text=lorebook_text,
                    rag_text=rag_text,
                    history_text=history_text,
                    player_text=player_text,
                    fixed_prompt=fixed_prompt,
                    state=state,
                    max_context=max_context,
                )
            except Exception as e:
                logger.warning(
                    "ContextEngine failed (stream), fallback to budget manager: %s", e
                )
                system_context = build_system_context_with_budget(
                    system_prompt="",
                    world_text=world_text,
                    npc_text=npc_text,
                    history_text=history_text,
                    lorebook_text=lorebook_text,
                    rag_text=rag_text,
                    player_text=player_text,
                    fixed_prompt=fixed_prompt,
                    max_context=max_context,
                    identity_text=identity_text,
                    scene=scene,
                )
        else:
            system_context = build_system_context_with_budget(
                system_prompt="",
                world_text=world_text,
                npc_text=npc_text,
                history_text=history_text,
                lorebook_text=lorebook_text,
                rag_text=rag_text,
                player_text=player_text,
                fixed_prompt=fixed_prompt,
                max_context=max_context,
                identity_text=identity_text,
                scene=scene,
            )

        _min_chars = int(narrative_max_chars * 0.8)
        _len_hint = f"至少{_min_chars}字"
        system_context += (
            f"\n🔴【玩家姓名 - 绝对禁止更改】玩家的名字是「{state.name}」，你必须自始至终用这个名字称呼玩家，绝对不能用任何其他名字！"
            f"\n【重要规则】叙事中出现的所有人物必须使用上面列出的名字和设定，绝对不允许自行编造新名字或篡改已有身份。"
            f"\n【名字一致性 - 最高优先级】同一个角色在整段叙事中必须自始至终使用同一个名字！禁止在不同段落中偷偷更换角色名字。一旦你给某个角色起了名字，就必须在整段叙事中保持一致！"
            f"\n【字数硬性要求 - 最高优先级】你必须写出{_len_hint}的叙事！这是硬性要求，绝对不能少于{_min_chars}字！请详细描写场景、对话、动作、心理活动，充分展开剧情。"
            f"\n请以小说笔法写一段叙事，描述玩家行动后的故事发展。"
            f"\n【最高优先级】只输出纯文本叙事，绝对不要输出JSON格式、不要输出代码块、不要输出任何结构化数据。直接开始写故事。"
            f"\n【连贯性规则】必须紧接上一段叙事的内容继续写，不要跳转到完全不同的场景或时间。如果上一段结尾是某个悬念或动作，本段要接着写下去。"
            f"\n【角色区分规则】多人场景中，每个角色只能执行自己的动作。丫鬟≠小姐，仆人≠主人。禁止张冠李戴。"
            f"\n\n【玩家输入 - 作为叙事种子展开，不要重复或回应】\n{player_input}"
        )

        messages = [
            {"role": "system", "content": stream_system_prompt},
            {"role": "system", "content": system_context},
        ]
        if narrative_history:
            recent = narrative_history[-8:] if len(narrative_history) > 8 else narrative_history
            for entry in recent:
                pi = entry.get("player_input", "")[:400]
                text = entry.get("text", "")[:800]
                if pi:
                    messages.append({"role": "user", "content": pi})
                if text:
                    messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": player_input})

        return self.llm.chat_stream(messages, temperature=0.8)

    def generate_full_response(self, state: PlayerState, player_input: str,
                               world_state: dict = None, day: int = 1,
                               npc_names: list[str] = None,
                               npc_states: dict = None,
                               narrative_history: list[dict] = None,
                               fixed_prompt: str = "",
                               max_context: int = 32768,
                               strip_gray: bool = True,
                               scene_type=None,
                               narrative_max_chars: int = 1000) -> dict:
        npc_text = build_npc_context(npc_states, player_input, world_state) if npc_states else ""
        world_text = build_world_context(world_state) if world_state else ""
        player_text = build_player_context(state, world_state)
        history_text = build_history_context(player_input, narrative_history) if narrative_history else ""
        lorebook_text = self.lorebook.to_prompt(player_input) if self.lorebook else ""
        rag_text = self._build_rag_context(player_input, scene_type=scene_type,
                                            npc_states=npc_states, world_state=world_state,
                                            current_day=day)
        identity_text = self.memory.get_identity_context() if self.memory else ""
        scene = detect_scene(player_input, narrative_history)

        # [v9] 根据金手指设置格式化 SYSTEM_PROMPT（提前构建，便于缓存前缀记录）
        golden_finger_rule = self._get_golden_finger_rule(world_state)
        formatted_system_prompt = SYSTEM_PROMPT.format(
            golden_finger_rule=golden_finger_rule
        )
        # [v10.6] 用配置的叙事字数替换 SYSTEM_PROMPT 中的硬编码 "500-1000字"
        _min_chars = narrative_max_chars // 2
        _len_hint = f"至少{_min_chars}字，最多{narrative_max_chars}字"
        formatted_system_prompt = formatted_system_prompt.replace("500-1000字", _len_hint)
        # [Bug] 玩家身份锚点：在系统提示顶部强制定义玩家姓名，防止 LLM 幻觉改名
        # 这是防止"角色名漂移"的最后一道防线，与 player_context 层互补
        player_anchor = (
            f"\n\n【玩家身份锚点 - 最高优先级】\n"
            f"玩家姓名：{state.name}\n"
            f"玩家身份：{state.social.position}\n"
            f"叙事中必须始终使用「{state.name}」称呼玩家，绝对禁止改名。"
        )
        formatted_system_prompt = formatted_system_prompt + player_anchor

        # [v10++] 优先使用 ContextEngine（注意力预算 + 压缩 + Prompt Caching）
        # 失败时回退到 build_system_context_with_budget，保持向后兼容
        used_context_engine = False
        if self.context_engine:
            try:
                system_context = self._build_context_with_engine(
                    system_prompt=formatted_system_prompt,
                    world_text=world_text,
                    npc_text=npc_text,
                    identity_text=identity_text,
                    lorebook_text=lorebook_text,
                    rag_text=rag_text,
                    history_text=history_text,
                    player_text=player_text,
                    fixed_prompt=fixed_prompt,
                    state=state,
                    max_context=max_context,
                )
                used_context_engine = True
            except Exception as e:
                logger.warning(
                    "ContextEngine failed, fallback to budget manager: %s", e
                )
                system_context = build_system_context_with_budget(
                    system_prompt="",
                    world_text=world_text,
                    npc_text=npc_text,
                    history_text=history_text,
                    lorebook_text=lorebook_text,
                    rag_text=rag_text,
                    player_text=player_text,
                    fixed_prompt=fixed_prompt,
                    max_context=max_context,
                    identity_text=identity_text,
                    scene=scene,
                )
        else:
            system_context = build_system_context_with_budget(
                system_prompt="",
                world_text=world_text,
                npc_text=npc_text,
                history_text=history_text,
                lorebook_text=lorebook_text,
                rag_text=rag_text,
                player_text=player_text,
                fixed_prompt=fixed_prompt,
                max_context=max_context,
                identity_text=identity_text,
                scene=scene,
            )

        # 存储上下文调试信息供前端面板使用
        lorebook_matches_count = self.lorebook.match_count(player_input) if self.lorebook else 0
        lorebook_entries = []
        if self.lorebook:
            matched = self.lorebook.match(player_input)
            for pos in ["before_main", "after_main", "depth_inject"]:
                for item in matched.get(pos, []):
                    lorebook_entries.append(item[:150])
        rag_results = []
        if self.memory:
            raws = self.memory.search_memory(player_input, n_results=5)
            for r in raws:
                if r.get("text"):
                    rag_results.append(r["text"][:150])
        self._last_context_debug = {
            "total_estimated_tokens": estimate_tokens(system_context),
            "world_context": world_text[:300] if world_text else "",
            "world_tokens": estimate_tokens(world_text),
            "npc_context": npc_text[:300] if npc_text else "",
            "npc_count": len(npc_states) if npc_states else 0,
            "npc_tokens": estimate_tokens(npc_text),
            "player_context": player_text[:300],
            "player_tokens": estimate_tokens(player_text),
            "history_turns": len(narrative_history) if narrative_history else 0,
            "history_tokens": estimate_tokens(history_text),
            "lorebook_matches": lorebook_matches_count,
            "lorebook_entries": lorebook_entries,
            "lorebook_tokens": estimate_tokens(lorebook_text),
            "rag_results": rag_results,
            "rag_tokens": estimate_tokens(rag_text),
            "fixed_prompt": fixed_prompt[:200] if fixed_prompt else "",
            "fixed_prompt_tokens": estimate_tokens(fixed_prompt),
            "max_context": max_context,
            # [v10++] 上下文工程调试信息
            "context_engine_used": used_context_engine,
            "cache_stats": (
                self.context_engine.cache.get_stats()
                if used_context_engine and self.context_engine else None
            ),
        }

        system_context += (
            f"\n🔴【玩家姓名 - 绝对禁止更改】玩家的名字是「{state.name}」，你必须自始至终用这个名字称呼玩家，绝对不能用任何其他名字！这是最高优先级规则！"
            f"\n【重要规则】叙事中出现的所有人物必须使用上面列出的名字和设定，绝对不允许自行编造新名字或篡改已有身份。"
            f"\n【名字一致性 - 最高优先级】同一个角色在整段叙事中必须自始至终使用同一个名字！禁止在不同段落中偷偷更换角色名字。一旦你给某个角色起了名字，就必须在整段叙事中保持一致！"
            f"\n【概念一致性 - 最高优先级】上文中出现的'系统'、'面板'、'属性'等概念必须保持完全一致的表述！如果上文写的是'系统面板'，下文必须继续用'系统面板'，不允许改成'系统界面'、'属性面板'或其他表述！"
            f"\n【连贯性规则】必须紧接上一段叙事的内容继续写，不要跳转到完全不同的场景或时间。如果上一段结尾是某个悬念或动作，本段要接着写下去。"
            f"\n【角色区分规则】多人场景中，每个角色只能执行自己的动作。丫鬟≠小姐，仆人≠主人。禁止张冠李戴。"
            f"\n【禁止自查】叙事中绝对不允许出现'上一段提到的'、'前文'、'刚才'等元叙事词汇，直接继续写故事即可。"
            f"\n\n【玩家输入 - 作为叙事种子展开，不要重复或回应】\n{player_input}"
        )

        # formatted_system_prompt 已在上方提前构建（用于缓存前缀记录）

        messages = [
            {"role": "system", "content": formatted_system_prompt},
            {"role": "system", "content": system_context},
        ]
        if narrative_history:
            recent = narrative_history[-6:] if len(narrative_history) > 6 else narrative_history
            for entry in recent:
                pi = entry.get("player_input", "")[:300]
                text = entry.get("text", "")[:800]
                if pi:
                    messages.append({"role": "user", "content": pi})
                if text:
                    messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": player_input})

        # [v10] 优先使用结构化输出（叙事 schema），失败回退到 chat_json_from_messages
        _narrative_hint = f"至少{_min_chars}字，最多{narrative_max_chars}字"
        response = {}
        if hasattr(self.llm, "chat_structured"):
            try:
                structured_prompt = "\n".join([f"[{m['role']}]: {m['content']}" for m in messages])
                response = self.llm.chat_structured(structured_prompt, "narrative", temperature=0.4,
                                                    narrative_hint=_narrative_hint, task_type=TASK_DIALOGUE)
            except Exception as e:
                logger.warning("Structured narrative generation failed, fallback: %s", e)
                response = {}

        if "error" in response or not response.get("narrative"):
            response = self.llm.chat_json_from_messages(messages, temperature=0.4,
                                                        narrative_hint=_narrative_hint, task_type=TASK_DIALOGUE)

        if "error" in response or not response.get("narrative"):
            logger.info("LLM response missing narrative, retrying with simplified prompt")
            retry_messages = list(messages)
            simplified_hint = (
                "\n\n【必须】只输出一个JSON，格式："
                '{"narrative":"你的叙事内容","options":[{"id":"A","text":"选项","type":"action","risk":"low"},'
                '{"id":"B","text":"选项","type":"action","risk":"medium"},'
                '{"id":"C","text":"选项","type":"action","risk":"high"}]}'
                f"\n不要加其他字段。narrative必须是{_narrative_hint}的小说体叙事。"
            )
            if retry_messages and retry_messages[-1]["role"] == "user":
                retry_messages[-1] = {"role": "user", "content": retry_messages[-1]["content"] + simplified_hint}
            response = self.llm.chat_json_from_messages(retry_messages, temperature=0.3,
                                                        narrative_hint=_narrative_hint, task_type=TASK_DIALOGUE)

        if "error" in response or not response.get("narrative"):
            response["narrative"] = self._fallback_narrative(state, player_input, world_state)
            if not response.get("options"):
                response["options"] = self._fallback_options(state)
            response["_fallback"] = True
            # [改善] 保存原始输入，供前端重试按钮使用
            response["_retry_input"] = player_input

        # [Bug] 叙事有效但选项为空时，优先基于叙事上下文生成选项，而非直接使用硬编码fallback
        if response.get("narrative") and not response.get("options"):
            try:
                # 基于刚生成的叙事内容，单独调用LLM生成上下文相关的选项
                recent_narrative = response["narrative"][-800:]
                context_options_prompt = OPTIONS_PROMPT.format(
                    state_summary=self._build_state_summary(state, world_state),
                    tags=", ".join(state.tags[:15]),
                )
                context_options_prompt += (
                    f"\n\n【最近叙事内容 - 选项必须与之相关】\n{recent_narrative}\n\n"
                    f"【重要】根据上面的叙事内容，生成3个与当前剧情紧密相关的选项。"
                    f"选项必须回应叙事中提出的问题、矛盾或悬念，不要给出通用的'环顾四周'、'找人聊天'等无关选项。"
                )
                options_response = self.llm.chat_json(context_options_prompt, temperature=0.85, task_type=TASK_DIALOGUE)
                options = options_response.get("options", [])
                if options and len(options) >= 2:
                    response["options"] = options
                else:
                    response["options"] = self._fallback_options(state)
            except Exception as e:
                logger.warning("Context-aware options generation failed: %s", e)
                response["options"] = self._fallback_options(state)

        if response.get("status_changes"):
            self.apply_status_changes(state, response["status_changes"])
        if response.get("new_tags") or response.get("removed_tags"):
            self.apply_tags(state, response.get("new_tags", []), response.get("removed_tags", []))
        if response.get("new_effects") or response.get("removed_effects"):
            self.apply_effects(state, response.get("new_effects", []), response.get("removed_effects", []))
        if response.get("relation_changes"):
            self.apply_relation_changes(state, response["relation_changes"],
                                        npc_names=npc_names,
                                        npc_states=npc_states,
                                        day=day)
        if response.get("identity_changes") and npc_states:
            identity_log = self.apply_identity_changes(
                response["identity_changes"], npc_states, day
            )
            if identity_log:
                response["_identity_log"] = identity_log

        narrative = response.get("narrative", "")
        if narrative:
            if strip_gray:
                narrative = self._clean_narrative(narrative)
            response["narrative"] = narrative
            # [Bug#21] 移除此处的 update_memory — TurnProcessorV2.process() 会统一调用，
            # 此处重复调用导致同一叙事被存储两次

        return response

    def generate_metadata_from_narrative(self, state: PlayerState, player_input: str,
                                         narrative: str, npc_names: list[str] = None,
                                         narrative_history: list[dict] = None,
                                         world_state: dict = None,
                                         npc_states: dict = None) -> dict:
        """[v10.6] 阶段 2：基于已完成的叙事文本，生成选项/状态变化等结构化元数据。
        用于流式模式下，叙事已通过 generate_narrative_stream 输出完毕后的第二次 LLM 调用。
        [v11] 切换为 TASK_DIALOGUE（对话模型），确保选项与叙事上下文紧密关联。"""
        # 提取当前场景信息
        current_location = world_state.get("current_location", "未知地点") if world_state else "未知地点"
        current_day = world_state.get("current_day", 1) if world_state else 1
        current_time = world_state.get("current_time", "白天") if world_state else "白天"

        # 构建在场NPC信息
        present_npcs = []
        if npc_states:
            for npc_id, npc_data in npc_states.items():
                npc_name = npc_data.get("name", npc_id) if isinstance(npc_data, dict) else getattr(npc_data, "name", npc_id)
                npc_role = npc_data.get("role", "") if isinstance(npc_data, dict) else getattr(npc_data, "role", "")
                present_npcs.append(f"{npc_name}（{npc_role}）")

        # 提取最近叙事中的关键信息（场景、人物、情绪）
        recent_context = ""
        if narrative_history and len(narrative_history) > 0:
            # [Bug] narrative_history 字段是 "text" 不是 "narrative"
            last_narrative = narrative_history[-1].get("text", "") if isinstance(narrative_history[-1], dict) else ""
            if last_narrative:
                # 优先取末尾 500 字，剧情发展通常在后半段
                tail = last_narrative[-500:] if len(last_narrative) > 500 else last_narrative
                recent_context = f"\n【上一回合叙事末尾】\n{tail}\n"

        # 提取叙事结尾的关键信息，帮助LLM锚定选项
        narrative_tail = narrative[-1500:] if len(narrative) > 1500 else narrative
        last_paragraph = ""
        paragraphs = narrative_tail.strip().split("\n")
        for p in reversed(paragraphs):
            if p.strip():
                last_paragraph = p.strip()
                break

        prompt = (
            f"【当前场景信息】\n"
            f"地点：{current_location}\n"
            f"时间：第{current_day}天，{current_time}\n"
            f"在场人物：{', '.join(present_npcs) if present_npcs else '仅有主角'}\n"
            f"主角状态：{state.name}（{state.social.position}），健康={state.stats.health if state.stats else 100}\n"
            f"{recent_context}"
            f"\n【主角刚执行的行动】\n{player_input}\n"
            f"\n【本轮生成的叙事（最后500字）】\n{narrative_tail[-500:]}\n"
            f"\n【叙事最后一句】\n{last_paragraph}\n"
            f"\n【任务】根据叙事最后一句的场景，生成3个后续行动选项。\n"
            f"\n【步骤一：分析叙事结尾】先回答：叙事最后发生了什么？谁在场？有什么未完成的动作或对话？\n"
            f"【步骤二：生成选项】每个选项必须直接回应步骤一的答案。\n"
            f"\n【铁律 - 违反则作废】\n"
            f"1. 选项必须紧接叙事最后一句的局面，不能跳到其他场景\n"
            f"2. 如果叙事最后有人对主角说话，至少一个选项必须是回应这个人\n"
            f"3. 如果叙事最后描述了某个具体情境（如宴会进行中、战斗中、谈判中），选项必须围绕这个情境\n"
            f"4. 选项必须符合当前地点「{current_location}」，不能做这个地点不允许的事\n"
            f"5. 禁止输出通用选项：观察四周、整理思绪、制定计划、找人聊天、主动出击、打破僵局、"
            f"去草坪转转、去厨房看看、找个地方歇息、四处走走、看看周围、和附近的人交谈\n"
            f"6. 选项要具体：说谁、做什么、怎么做，不要模糊描述\n"
            f"\n【好选项示例】叙事最后「花无缺端来一盘烤排骨，张立吃得正香」→ "
            f"选项应是「夸赞花无缺的手艺，让她再烤一盘」而不是「去厨房看看」\n"
            f"【坏选项示例】叙事最后「众人在宴会上欢笑」→ 「去草坪转转」是坏选项，因为主角还在宴会上\n"
            f"\n请输出JSON：\n"
            f'{{"analysis":"叙事结尾分析（一句话）","options":[{{"id":"A","text":"选项描述","type":"action","risk":"low/medium/high"}},'
            f'{{"id":"B","text":"选项描述","type":"action","risk":"low/medium/high"}},'
            f'{{"id":"C","text":"选项描述","type":"action","risk":"low/medium/high"}}]}}\n'
        )
        response = self.llm.chat_json(prompt, temperature=0.4, task_type=TASK_DIALOGUE)
        response["narrative"] = narrative
        return response

    def audit_identity_consistency(self, narrative: str, npc_states: dict,
                                   day: int = 0) -> list[dict]:
        """
        身份审计：检查叙事中各NPC的身份是否与数据库一致。
        如果LLM悄悄改了身份但没在identity_changes中标记，此处兜底捕获。

        返回: [{npc_name, db_role, narrative_role, discrepancy, auto_fix}]
        """
        if not narrative or not npc_states:
            return []

        # 构建NPC身份清单
        npc_inventory = []
        for nid, npc in npc_states.items():
            summary = npc.get_identity_summary()
            npc_inventory.append(f"  {nid}: {summary}")

        audit_prompt = f"""你是角色身份审计员。根据下面的【数据库身份档案】和【新生成的叙事】，检查叙事中每个NPC的身份是否与数据库一致。

【数据库身份档案】（这些是权威的当前身份）
{chr(10).join(npc_inventory)}

【新生成的叙事】
{narrative[:1200]}

【审计规则】
1. 只关注实质性的身份变化：职业变更（屠夫→捕快）、关系变更（妻子→前妻）、地位变更（小兵→将军）
2. 不关注临时的、一次性的行为（如"今天帮邻居看店"不算职业变更）
3. 如果叙事中某NPC的身份与数据库一致，不用报告
4. 如果叙事中某NPC出现了身份变化但数据库未体现，必须报告
5. 如果叙事中完全没提到某NPC，不用报告

【输出JSON格式】
{{
    "discrepancies": [
        {{
            "npc_id": "NPC的ID或名字",
            "db_role": "数据库记录的身份",
            "narrative_role": "叙事中呈现的身份",
            "reason": "身份变化的剧情原因（从叙事中提取，一句话）",
            "is_legitimate_change": true,  // 这是合理的剧情演变，还是AI的错误？
            "suggested_fix": "建议的新身份（如果是合理演变）"
        }}
    ]
}}

如果叙事中所有人物的身份都与数据库一致，返回 {{"discrepancies": []}}。
只输出JSON。"""

        try:
            result = self.llm.chat_json(audit_prompt, temperature=0.2, max_tokens=0, task_type=TASK_SIMPLE)
            discrepancies = result.get("discrepancies", [])
            return discrepancies
        except Exception as e:
            logger.warning("Identity audit LLM failed: %s", e)
            return []

    def _clean_narrative(self, text: str) -> str:
        """后处理：去掉JSON/markdown标记、正文后面的灰色旁白/总结/预测"""
        import re
        text = text.strip()
        # 去掉markdown代码块标记
        text = re.sub(r'^```json\s*', '', text)
        text = re.sub(r'^```\s*$', '', text, flags=re.MULTILINE)
        text = re.sub(r'```$', '', text)
        # 去掉JSON字段包裹（如 "narrative": "..."）
        if text.startswith('{') and '"narrative"' in text:
            m = re.search(r'"narrative"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.DOTALL)
            if m:
                text = m.group(1).replace('\\n', '\n').replace('\\"', '"')

        paragraphs = re.split(r'\n\s*\n', text)
        if len(paragraphs) <= 1:
            # 单段落模式：检查是否是预测/总结性文本
            # 如果文本过长且包含预测性词汇，截断
            if len(text) > 600:
                prediction_markers = [
                    '这一', '这导致', '此后', '最终', '后来',
                    '多年以后', '几年后', '多年后', '最终导致',
                    '可能引发', '将会', '势必', '必然',
                    '消息迅速', '流言四起', '一时之间',
                ]
                if any(marker in text for marker in prediction_markers):
                    # 收集所有 idx > 200 的标记，取最早位置截断
                    indices = []
                    for marker in prediction_markers:
                        idx = text.find(marker)
                        if idx > 200:
                            indices.append(idx)
                    if indices:
                        return text[:min(indices)].strip()
            return text.strip()

        main_parts = []
        for p in paragraphs:
            p = p.strip()
            if not p:
                continue
            # [v9] 增强灰色文本检测模式
            skip_patterns = [
                # 时间跳转/总结
                r'春季的', r'夏季的', r'秋季的', r'冬季的',
                r'随着', r'此后', r'从此', r'因此', r'这一决策导致',
                r'原本应由', r'导致了', r'已不可逆转',
                r'市民们从', r'民众对', r'为后续',
                r'内部动荡', r'笼罩着', r'留下了伏笔',
                r'然而', r'尽管', r'尽管危机',
                r'这一', r'这导致', r'这使得',
                # [v9] 新增：预测/旁白/总结模式
                r'消息迅速', r'流言四起', r'一时之间',
                r'可能引发', r'将会引发', r'势必',
                r'多年以后', r'几年后', r'多年后',
                r'最终导致', r'最终成为', r'成为了',
                r'如同.*一颗石子', r'激起了.*涟漪',
                r'成为蝴蝶效应', r'蝴蝶效应的起点',
                r'影响.*朝堂格局', r'引发.*权力斗争',
            ]
            is_gray = any(re.search(pat, p) for pat in skip_patterns)
            if is_gray and main_parts:
                break
            main_parts.append(p)
        return "\n\n".join(main_parts).strip()

    def _get_golden_finger_rule(self, world_state=None) -> str:
        """[v9] 根据金手指设置生成规则文本（所有世界类型统一逻辑）"""
        # 从world_state获取金手指设置
        golden_finger = False
        world_type = "custom"
        if world_state:
            if isinstance(world_state, dict):
                golden_finger = world_state.get("golden_finger", False)
                world_type = world_state.get("world_type", "custom")
            else:
                golden_finger = getattr(world_state, "golden_finger", False)
                world_type = getattr(world_state, "world_type", "custom")

        if golden_finger:
            return (
                "玩家已选择开启金手指。你可以根据玩家的描述生成系统面板、超能力、"
                "现代物品具现化等金手指功能。但要注意：金手指应该有合理的限制和代价，"
                "不能无限制使用。金手指的内容应该与世界设定融合，不能过于突兀。"
            )
        else:
            # 根据世界类型生成具体的否定规则
            world_rules = {
                "historical": "历史穿越世界严格遵循历史逻辑。绝对没有系统面板、属性查看、空间戒指、现代物品具现化、超能力。",
                "modern": "现代生活世界严格遵循现实逻辑。绝对没有超能力、魔法、系统面板。",
                "wuxia": "武侠世界遵循传统武侠设定。有内力武功，但没有系统面板、没有现代科技、没有修仙。",
                "xianxia": "修仙世界遵循传统修仙设定。有灵气法宝，但没有系统面板、没有现代科技。",
                "fantasy": "奇幻世界遵循传统奇幻设定。有魔法种族，但不允许系统面板等meta元素。",
                "scifi": "科幻世界遵循硬科幻逻辑。有高科技，但不允许超自然元素。",
                "postapocalyptic": "末日世界遵循现实逻辑。资源稀缺，不允许超自然元素。",
                "urban_fantasy": "都市异能世界允许超能力，但不允许系统面板等meta元素。",
            }
            world_rule = world_rules.get(world_type, "该世界严格遵循既有设定，不允许超出设定的元素。")

            return (
                f"【绝对禁止金手指】玩家选择关闭金手指。{world_rule}\n"
                "- 绝对不允许出现系统面板、属性查看、空间戒指、超能力\n"
                "- 绝对不允许穿越者凭空变出现代物品（手机、手枪、电脑等）\n"
                "- 绝对不允许读取NPC属性、好感度、忠诚度等数据化信息\n"
                "- 如果玩家试图使用金手指，必须用自然的叙事否定：\n"
                "  例：'你在脑海中呼唤了无数次，回应你的只有沉默。什么系统面板，统统没有出现。'\n"
                "  例：'你集中精神盯着抽屉，几乎把脑浆都想沸腾了。打开后，里面只有几张宣纸和一枚私印。'\n"
                "  例：'你以为自己是话本里的主角，会有奇遇？可惜现实不是小说。'\n"
                "- 穿越者的优势仅限于：历史知识、现代思维、跨文化视角\n"
                "- 这是硬性规则，绝对不可违反"
            )

    def _fallback_narrative(self, state: PlayerState, player_input: str, world_state=None) -> str:
        """LLM失败时的安全回退叙事。
        [改善] 更友好的占位叙事，提示玩家可以重试。"""
        return (
            f"你陷入了沉思，脑海中思绪翻涌却难以成形。"
            f"周围的一切仿佛静止了片刻，{resolve_location_name(state.location, world_state)}的风声依旧。\n\n"
            f"（AI 服务暂时繁忙，你可以点击「重试」重新生成，或选择下方选项继续）"
        )

    def _fallback_options(self, state: PlayerState) -> list[dict]:
        """LLM失败时的安全回退选项 — 尽量根据玩家状态生成更有针对性的选项"""
        loc = state.location if state.location else "附近"
        # 根据玩家当前状态生成更有针对性的回退选项
        options = []
        # A: 稳健选项 — 观察环境
        options.append({
            "id": "A", "text": f"仔细观察{loc}周围的情况，寻找有用的信息",
            "type": "search", "risk": "low", "needs_dice": False,
            "hint": "了解当前处境，可能发现线索"
        })
        # B: 互动选项 — 根据关系选择
        if state.relations:
            top_rel = max(state.relations.items(), key=lambda x: x[1].favor if hasattr(x[1], 'favor') else 0)
            npc_name = top_rel[1].name if hasattr(top_rel[1], 'name') else top_rel[0]
            options.append({
                "id": "B", "text": f"向{npc_name}询问当前局势，寻求建议",
                "type": "talk", "risk": "medium", "needs_dice": False,
                "hint": "借助可信赖之人的信息做出判断"
            })
        else:
            options.append({
                "id": "B", "text": "整理思绪，回顾当前掌握的信息，制定下一步计划",
                "type": "action", "risk": "low", "needs_dice": False,
                "hint": "冷静分析当前局势"
            })
        # C: 骚操作 — 根据属性选择
        if state.stats.strength >= 25:
            options.append({
                "id": "C", "text": "直接用行动表态，展示你的实力和决心",
                "type": "action", "risk": "high", "needs_dice": True,
                "dice_stat": "strength", "dice_difficulty": 12,
                "hint": "以力破局，可能震慑对手也可能激化矛盾"
            })
        elif state.stats.intelligence >= 20:
            options.append({
                "id": "C", "text": "抛出一个出人意料的问题或条件，试探对方底线",
                "type": "custom", "risk": "high", "needs_dice": True,
                "dice_stat": "intelligence", "dice_difficulty": 11,
                "hint": "以智取胜，可能掌握主动权"
            })
        else:
            options.append({
                "id": "C", "text": "做出一个大胆的举动，打破僵局",
                "type": "custom", "risk": "high", "needs_dice": True,
                "dice_stat": "luck", "dice_difficulty": 12,
                "hint": "全凭运气和直觉"
            })
        return options

    # ── [v11] P0: 规则化线索扩展 ────────────────────────────

    def _build_entity_clue_index(self, npc_states: dict = None,
                                  world_state=None) -> dict[str, list[str]]:
        """构建实体→线索倒排索引，从NPC设定和世界状态中提取关联线索。
        用于在玩家输入提到某实体时，自动扩展检索query。
        [v11] 缓存优化：NPC状态不变时不重建。"""
        # 计算当前NPC状态的哈希值，判断是否需重建缓存
        import hashlib as _hl
        state_hash = 0
        if npc_states:
            # 用NPC数量和名字列表做快速哈希
            state_hash = hash((len(npc_states), tuple(sorted(npc_states.keys()))))
        if (self._entity_index_cache is not None
                and self._entity_index_cache_hash == state_hash):
            return self._entity_index_cache

        index: dict[str, list[str]] = {}

        # 从NPC状态提取
        if npc_states:
            for npc_id, npc in npc_states.items():
                name = npc.name if hasattr(npc, 'name') else npc_id
                clues = []
                if hasattr(npc, 'role') and npc.role:
                    clues.append(f"职业是{npc.role}")
                if hasattr(npc, 'personality') and npc.personality:
                    clues.append(f"性格{npc.personality[:40]}")
                if hasattr(npc, 'current_location') and npc.current_location:
                    loc = resolve_location_name(npc.current_location, world_state)
                    clues.append(f"在{loc}")
                rel = getattr(npc, 'relation_to_player', None)
                if rel and hasattr(rel, 'relation_type') and rel.relation_type:
                    clues.append(f"与玩家关系是{rel.relation_type}")
                if hasattr(npc, 'tags') and npc.tags:
                    clues.append(f"标签:{','.join(npc.tags[:3])}")
                if clues:
                    index[name] = clues
                    # 也按昵称/简称索引
                    if len(name) > 1:
                        index[name[:2]] = clues

        # [v11] 缓存索引
        self._entity_index_cache = index
        self._entity_index_cache_hash = state_hash
        return index

    # ── [v11] P1: LLM 查询扩展 ─────────────────────────────

    def _expand_query_with_llm(self, player_input: str, location: str = "",
                                time_str: str = "", npc_states: dict = None,
                                player_state=None) -> dict | None:
        """使用 cheap LLM 将玩家输入扩展为结构化查询。
        返回 {"entities": [...], "intent": "...", "time_range": "...", "expanded_terms": [...]}
        失败时返回 None（调用方回退到规则扩展）。
        [v11] 缓存结果，相同输入 60 秒内不重复调用。"""
        import time as _time
        import hashlib as _hl

        # [v11] 缓存检查
        cache_key = _hl.md5(player_input.strip()[:200].encode()).hexdigest()
        if cache_key in self._llm_expansion_cache:
            ts, val = self._llm_expansion_cache[cache_key]
            if _time.time() - ts < self._llm_expansion_cache_ttl:
                return val

        from .prompt.player_prompts import QUERY_EXPANSION_PROMPT

        # 构建 NPC 名字列表
        npc_names_str = ""
        if npc_states:
            names = [n.name for n in npc_states.values() if hasattr(n, 'name')]
            npc_names_str = "、".join(names[:20])

        tags_str = ""
        if player_state and hasattr(player_state, 'tags') and player_state.tags:
            tags_str = "、".join(player_state.tags[:10])

        prompt = QUERY_EXPANSION_PROMPT.format(
            location=location or "未知",
            time=time_str or "未知",
            npc_names=npc_names_str or "无",
            tags=tags_str or "无",
            player_input=player_input,
        )

        try:
            # 使用 TASK_SIMPLE 路由到 cheap LLM
            result = self.llm.chat_json(prompt, temperature=0.2, max_tokens=2048,
                                        task_type=TASK_SIMPLE)
            if isinstance(result, dict) and "error" not in result:
                self._llm_expansion_cache[cache_key] = (_time.time(), result)
                return result
        except Exception as e:
            logger.debug("LLM query expansion failed (fallback to rule): %s", e)
        # 失败也缓存 None，避免重复调用
        self._llm_expansion_cache[cache_key] = (_time.time(), None)
        return None

    def _expand_query_with_clues(self, player_input: str,
                                  entity_index: dict[str, list[str]],
                                  foreshadow_lifecycle=None) -> str:
        """根据玩家输入中的实体，扩展检索query以包含相关线索。
        零延迟，纯规则匹配。"""
        expanded_terms = []

        # 1. 实体匹配：从entity_index中查找提到的实体
        for entity, clues in entity_index.items():
            if len(entity) >= 2 and entity in player_input:
                expanded_terms.extend(clues[:2])  # 每个实体最多取2条线索

        # 2. 伏笔匹配：从活跃伏笔中查找相关线索
        if foreshadow_lifecycle:
            try:
                active = foreshadow_lifecycle.get_active_hooks()
                for hook in active[:5]:  # 最多检查5个活跃伏笔
                    content = hook.get("content", "")
                    # [Bug#30] 按标点和空格分词，而非逐字符迭代（逐字符 len 永远为 1）
                    import re as _re
                    words = _re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}', content)
                    for word in words:
                        if word in player_input:
                            expanded_terms.append(content[:60])
                            break
            except Exception:
                pass

        if expanded_terms:
            # 将扩展项追加到原始输入后面，用分号分隔
            return player_input + " " + " ".join(expanded_terms)
        return player_input

    def _build_rag_context(self, player_input: str, scene_type=None,
                            npc_states: dict = None, world_state=None,
                            current_day: int = 0) -> str:
        """从向量库检索相关历史和伏笔，构建RAG上下文。
        [v10+] 优先使用混合检索（BM25 + 向量 + GraphRAG），失败回退到纯向量检索。
        [v10+] scene_type: 叙事场景类型，用于动态调整检索权重（GraphRAG 动态启停）。
        [v10.6+] 使用缓存避免重复嵌入调用。
        [v11] P0: 检索前先用规则化线索扩展query，提升召回率。
        [v11] A+B+C: LLM查询扩展 + 知识图谱时间索引 + 增强排序融合。
        """
        if not self.memory:
            return ""

        # 兼容 SceneType 枚举与字符串
        scene_key = ""
        if scene_type is not None:
            scene_key = scene_type.value if hasattr(scene_type, "value") else str(scene_type)
        cache_key_input = player_input + scene_key
        cached = self._get_rag_cache(cache_key_input)
        if cached is not None:
            return cached

        # [v11] P1: LLM查询扩展（方案A）→ 提取实体、时间范围、扩展词
        # 失败时回退到规则扩展（P0）
        location = resolve_location_name(
            getattr(self, '_last_location', ''),
            world_state
        ) if world_state else ""
        time_str = ""
        if world_state and hasattr(world_state, 'current_day') and hasattr(world_state, 'current_time'):
            time_str = f"第{world_state.current_day}天 {world_state.current_time}"

        llm_expansion = self._expand_query_with_llm(
            player_input, location=location, time_str=time_str,
            npc_states=npc_states,
            player_state=getattr(self, '_player_state', None),
        )

        entity_hints = []
        time_range = "all"
        if llm_expansion and isinstance(llm_expansion, dict):
            # 提取实体名列表（用于图谱查询和排序）
            entities = llm_expansion.get("entities", [])
            entity_hints = [e.get("name", "") for e in entities if e.get("name")]
            time_range = llm_expansion.get("time_range", "all")
            # 扩展后的检索查询
            expanded_terms = llm_expansion.get("expanded_terms", [])
            if expanded_terms:
                player_input_with_expansion = player_input + " " + " ".join(expanded_terms)
            else:
                player_input_with_expansion = player_input
        else:
            # [v11] P0: 规则化线索扩展（回退）
            entity_index = self._build_entity_clue_index(npc_states, world_state)
            player_input_with_expansion = self._expand_query_with_clues(
                player_input, entity_index, self.foreshadow_lifecycle
            )

        parts = []

        # 计算时间窗口（用于图谱查询和排序）
        time_window_days = 0
        if time_range == "recent" and current_day > 0:
            time_window_days = 7
        elif time_range == "recent_long" and current_day > 0:
            time_window_days = 30

        # [v10+] 优先使用混合检索（如果已注入）
        narratives = None
        if self.hybrid_retriever is not None:
            try:
                # [v11] B+C: 传递 entity_hints 给图谱查询 + current_day 给排序
                narratives = self.hybrid_retriever.retrieve(
                    player_input_with_expansion, top_k=5, scene_type=scene_type,
                    current_day=current_day, entity_hints=entity_hints,
                )
                if narratives:
                    logger.debug("Hybrid retrieval returned %d results", len(narratives))
            except Exception as e:
                logger.warning("Hybrid retrieval failed, falling back to vector: %s", e)
                narratives = None

        # 回退：优先使用带三维度评分的 ranked 检索
        if not narratives:
            try:
                narratives = self.memory.search_memory_ranked(
                    player_input_with_expansion, n_results=5
                )
            except AttributeError:
                narratives = self.memory.search_memory(player_input_with_expansion, n_results=5)

        if narratives:
            nar_texts = [n["text"][:500] for n in narratives if n.get("text")]
            if nar_texts:
                parts.append("【向量库检索：相关历史】\n" + "\n".join([f"- {t}" for t in nar_texts]))

        foreshadows = self.memory.search_foreshadow(player_input_with_expansion, n_results=3)
        if foreshadows:
            fs_texts = [f["text"][:500] for f in foreshadows if f.get("text")]
            if fs_texts:
                parts.append("【向量库检索：伏笔/重要线索】\n" + "\n".join([f"- {t}" for t in fs_texts]))

        if not parts:
            result = ""
        else:
            result = "\n" + "\n".join(parts) + "\n请参考以上检索到的历史信息，保持叙事连贯性。\n"

        self._set_rag_cache(cache_key_input, result)
        return result

    def _build_state_summary(self, state: PlayerState, world_state: dict = None) -> str:
        important_tags = state.tags[:15]
        important_effects = state.status_effects[:8]
        top_relations = sorted(state.relations.items(), key=lambda x: -x[1].favor)[:5]

        lines = [
            f"姓名: {state.name}, 年龄: {state.age}",
            f"位置: {resolve_location_name(state.location, world_state)}",  # [Bug] location code → display name
            f"属性: 力量{state.stats.strength} 敏捷{state.stats.agility} "
            f"智力{state.stats.intelligence} 幸运{state.stats.luck}",
            f"生命: {state.stats.health}/{state.stats.max_health} "
            f"体力: {state.stats.energy}/{state.stats.max_energy}",
            f"金币: {state.social.gold} 声望: {state.social.reputation}",
            f"标签: {', '.join(important_tags)}" + (f" +{len(state.tags)-15}" if len(state.tags) > 15 else ""),
            f"状态: {', '.join(important_effects) if important_effects else '正常'}",
            f"目标: {state.current_goal}",
        ]
        if top_relations:
            rels = [f"{v.name if hasattr(v, 'name') else k}(好感{v.favor})" for k, v in top_relations]
            lines.append(f"关系: {', '.join(rels)}")
        if world_state:
            lines.append(f"天气: {world_state.get('weather', '晴朗')}")
        return "\n".join(lines)

    def rest(self, state: PlayerState):
        heal = min(20, state.stats.max_health - state.stats.health)
        recover = min(30, state.stats.max_energy - state.stats.energy)
        state.stats.health += heal
        state.stats.energy += recover
        return {
            "health_recovered": heal,
            "energy_recovered": recover,
        }

    def _try_consolidate_identity(self, state: PlayerState):
        """尝试将短期记忆中的模式整合为长期身份特征"""
        if not self.memory or not self.llm:
            return
        try:
            recent = state.memory.short_term[-10:]
            if len(recent) < 5:
                return
            prompt = f"""分析以下角色的近期经历，提取稳定的身份特征。

【近期经历】
{chr(10).join(recent)}

【当前标签】
{', '.join(state.tags[:10])}

【输出JSON格式】
{{
    "values": ["价值观1", "价值观2"],
    "personality": ["性格特征1", "性格特征2"],
    "habits": ["习惯1"],
    "knowledge": ["积累的知识1"]
}}
只输出JSON。如果没有什么值得提取的，返回空数组。"""
            result = self.llm.chat_json(prompt, temperature=0.3, max_tokens=0, task_type=TASK_SIMPLE)
            # 存入身份集合
            for trait_type, key in [("values", "values"), ("personality", "personality"),
                                     ("habits", "habits"), ("knowledge", "knowledge")]:
                for item in result.get(key, []):
                    if item and len(item) > 2:
                        self.memory.add_identity_trait(trait_type, item, "consolidation")
            state.memory.long_term_identity.consolidation_count += 1
        except Exception as e:
            logger.debug("Identity consolidation failed: %s", e)
