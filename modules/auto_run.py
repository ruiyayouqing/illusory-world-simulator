"""
[v1.2] 自主运行引擎 — 让世界在没有玩家干预时自我演化。

核心思想（参考《一些想法的总结》"AI虚拟世界 + 主角叙事过滤器"）：
  虚拟世界持续推演时间 → 所有角色拥有独立人格、记忆、目标
  每轮世界迭代：角色感知环境、互相交互、产生冲突、做出行动
  定时执行叙事聚合器：筛选和主角强相关的事件，整理成连贯小说章节。

设计要点：
  1. 完整复用 WorldManager.advance_time 的 8 步副作用链（NPC演化/经济/势力/跨日/on_new_day/tick）
     不绕过任何世界推演副作用，与正常游戏完全一致。
  2. 混合型主角：默认被动观察，当 NPC 主动接触主角时触发一次轻量 LLM 代演对话。
  3. 每日收集"主角相关事件"（NPC主动接触 + 世界事件 + NPC演化涉及主角的事件），
     累积成 full_log，N 天结束后用 NarrativeEngine.generate_novel_chapter 一次性汇总成章节。
  4. 运行前自动存档保护（auto_before_autorun 槽位），异常可回滚。

不引入新概念：完全复用 WorldManager / world_tick / NarrativeEngine / npc_agent 现有接口。
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .game_engine import GameEngine

logger = logging.getLogger("chronoverse.auto_run")


# 触发 AI 代演对话的事件类型（NPC 主动接触主角的强互动类型）
# 见 world_event.py 的 PLAYER_EVENT_TEMPLATES
_INTERACTIVE_EVENT_TYPES = {
    "visit", "invite", "gift", "chat",          # high_favor
    "greet", "ask_help", "deliver_msg",          # neutral
    "provoke", "challenge", "threaten",          # enemy
    "cold_visit",                                 # low_favor
}


class AutoRunEngine:
    """自主运行引擎：让世界自我演化 N 天，围绕主角汇总成小说章节。

    用法：
        result = engine.auto_run_engine.run_days(days=10)
        print(result["chapter"])
    """

    def __init__(self, engine: "GameEngine"):
        self.engine = engine

    def run_days(self, days: int, options: Optional[dict] = None) -> dict:
        """让世界自主运行 N 天，返回完整小说章节 + 事件汇总。

        Args:
            days: 要推进的天数（建议 5-30）
            options: 预留扩展项，目前未使用

        Returns:
            {
                "chapter": str,           # N 天汇总的小说章节文本
                "days_advanced": int,     # 实际推进的天数
                "from_day": int,          # 起始日（推进前）
                "to_day": int,            # 结束日（推进后）
                "events_count": int,      # 收集的主角相关事件总数
                "interactions_count": int,# 触发的 AI 代演对话次数
                "aborted": bool,          # 是否中途异常退出
                "error": str,             # aborted=True 时的错误信息
            }
        """
        eng = self.engine
        options = options or {}

        # 参数校验
        if not isinstance(days, int) or days < 1:
            return {"error": "天数必须是正整数"}
        if days > 365:
            return {"error": "单次自主运行不超过 365 天"}
        if not eng.world_state or not eng.player_state:
            return {"error": "世界或主角未初始化"}
        if not eng._world_manager:
            return {"error": "WorldManager 未初始化"}

        from_day = eng.world_state.current_day
        logger.info("[AutoRun] 开始自主运行 %d 天（起始日 %d）", days, from_day)

        # Step 0: 运行前自动存档（保护点）
        try:
            eng.save_game("auto_before_autorun")
            logger.info("[AutoRun] 运行前已自动存档")
        except Exception as e:
            logger.warning("[AutoRun] 运行前存档失败（继续运行）: %s", e)

        # 收集每日事件 → 拼成 full_log 供最终章节生成使用
        daily_logs: list[str] = []
        total_events = 0
        total_interactions = 0
        aborted = False
        error_msg = ""

        for i in range(days):
            try:
                day_log, ev_count, ia_count = self._run_one_day()
                daily_logs.append(day_log)
                total_events += ev_count
                total_interactions += ia_count
            except Exception as e:
                logger.error("[AutoRun] 第 %d 天运行失败，中止: %s", i + 1, e, exc_info=True)
                aborted = True
                error_msg = str(e)
                break

        to_day = eng.world_state.current_day
        actual_days = to_day - from_day

        # Step 2: N 天结束后，把多日日志汇总成完整小说章节
        chapter = ""
        chapter_llm_failed = False
        try:
            chapter, chapter_llm_failed = self._generate_summary_chapter(
                daily_logs, from_day, to_day
            )
        except Exception as e:
            logger.error("[AutoRun] 章节汇总生成失败: %s", e, exc_info=True)
            chapter = f"（章节生成失败：{e}）\n\n" + "\n\n---\n\n".join(daily_logs)
            chapter_llm_failed = True

        # Step 3: 保存最终状态
        try:
            eng.save_game("auto_after_autorun")
        except Exception as e:
            logger.warning("[AutoRun] 运行后存档失败: %s", e)

        logger.info(
            "[AutoRun] 完成：%d 天，%d 个事件，%d 次代演对话，章节 %d 字%s",
            actual_days, total_events, total_interactions, len(chapter),
            "（LLM失败，已降级为日志摘要）" if chapter_llm_failed else "",
        )

        return {
            "chapter": chapter,
            "days_advanced": actual_days,
            "from_day": from_day,
            "to_day": to_day,
            "events_count": total_events,
            "interactions_count": total_interactions,
            "aborted": aborted,
            "error": error_msg,
            "chapter_llm_failed": chapter_llm_failed,  # [Bug] 章节是否为 LLM 失败兜底
        }

    # ===== 单日运行 =====

    def _run_one_day(self) -> tuple[str, int, int]:
        """运行一天，返回 (当日日志, 事件数, 代演对话数)

        一天的流程：
        1. WorldManager.advance_time() 完整 8 步副作用
        2. 若跨日，WorldManager.on_new_day()（含 world_tick.tick 生成事件）
        3. 收集今日主角相关事件：
           - PlayerEventBus.list_today（NPC 主动接触）
           - WorldEventBus.list_today（世界事件）
           - NPC 演化涉及主角的事件（来自 advance_time 返回的 npc_events）
        4. 对接触型事件触发 AI 代演对话（混合型主角）
        5. 标记事件为 accepted（避免堆积）
        6. [v1.2] 收集 NPC 即时反应事件（来自 EventBus）
        """
        eng = self.engine
        current_day_before = eng.world_state.current_day

        # [v1.2] 更新 npc_reaction_engine 的上下文引用（NPC 列表可能变化）
        self._refresh_reaction_context()

        # Step 1: 完整推进一个时段（advance_time 内部会判断是否跨日并触发 on_new_day）
        wm_result = eng._world_manager.advance_time()

        # Step 2: 若未跨日（单时段推进），主动调用 on_new_day 推进到下一天
        # WorldManager.advance_time 默认推进一个时段（如清晨→上午），
        # 多次调用直到跨日，确保 run_one_day 真正推进一整天。
        # 注意：advance_time 返回的 time_events 含 new_day 标记
        new_day_reached = bool(wm_result.get("time_events"))
        # 检查时间是否真的跨日（current_day 是否变化）
        if eng.world_state.current_day == current_day_before:
            # 未跨日，继续推进直到跨日（最多再推进 5 个时段防止死循环）
            for _ in range(5):
                if eng.world_state.current_day > current_day_before:
                    break
                try:
                    eng._world_manager.advance_time()
                except Exception as e:
                    logger.warning("[AutoRun] 推进时段失败: %s", e)
                    break

        current_day = eng.world_state.current_day

        # Step 3: 收集今日主角相关事件
        # PlayerEventBus.list_today 返回 trigger_day == current_day 的事件
        # 注意：world_tick.tick() 已经在 on_new_day 中生成并添加到 PlayerEventBus
        player_events = []
        if eng.player_event_bus:
            player_events = eng.player_event_bus.list_today(current_day)

        world_events = []
        if eng.world_event_bus:
            world_events = eng.world_event_bus.list_today(current_day)

        # NPC 演化事件（来自 advance_time 返回值，可能涉及主角）
        npc_events = wm_result.get("npc_events", []) or []

        # 睡眠事件
        sleeping_events = wm_result.get("sleeping_npc_events", []) or []

        # 战争事件
        war_events = wm_result.get("war_events", []) or []

        # Step 4: 对接触型事件触发 AI 代演对话（混合型主角）
        interactions = []
        for evt in player_events:
            if evt.event_type in _INTERACTIVE_EVENT_TYPES:
                dialogue = self._ai_act_for_event(evt)
                if dialogue:
                    interactions.append({
                        "npc": evt.payload.get("npc_name", "某人"),
                        "event_type": evt.event_type,
                        "title": evt.title,
                        "dialogue": dialogue,
                    })
            # 标记事件已处理（自主运行期间自动接受，不堆积 pending）
            try:
                eng.player_event_bus.mark(evt.event_id, "accepted")
            except Exception:
                pass

        # Step 5: [v1.2] 收集今日 NPC 即时反应事件
        reactions = self._collect_today_reactions(current_day)

        # Step 6: 拼接当日日志
        day_log = self._format_day_log(
            current_day,
            player_events,
            world_events,
            npc_events,
            sleeping_events,
            war_events,
            interactions,
            reactions,
        )

        return day_log, len(player_events), len(interactions)

    def _refresh_reaction_context(self):
        """[v1.2] 刷新 npc_reaction_engine 的上下文引用"""
        try:
            from .npc_reaction import get_npc_reaction_engine
            engine = get_npc_reaction_engine()
            engine._all_npcs = self.engine.npc_states
            engine._world_state = self.engine.world_state
            engine._player = self.engine.player_state
        except Exception as e:
            logger.debug("Refresh reaction context failed: %s", e)

    def _collect_today_reactions(self, current_day: int) -> list[dict]:
        """[v1.2] 收集今日 NPC 即时反应事件（来自 EventBus 触发）

        这些反应是 NPC 在场感知到 NPC 行动事件后做出的即时反应，
        已经由 npc_reaction_engine 自动处理并应用到 NPC 状态。
        这里仅从 NPC 的 recent_actions 中提取今日的反应记录，用于日志展示。
        """
        reactions = []
        for npc_id, npc in (self.engine.npc_states or {}).items():
            for action in (npc.recent_actions or []):
                if (action.get("day") == current_day
                        and action.get("action") == "react"):
                    detail = action.get("detail", "")
                    if detail:
                        reactions.append({
                            "npc_name": npc.name,
                            "detail": detail,
                        })
        # 限制条数避免日志过长
        return reactions[:8]

    # ===== AI 代演（混合型主角） =====

    def _ai_act_for_event(self, event) -> str:
        """对 NPC 主动接触事件，触发一次轻量 LLM 代演对话。

        使用 NPCAgent.interact_with_player，主角的"行动"由事件类型推断：
          - visit/invite/chat/greet/cold_visit → 礼貌回应
          - ask_help → 询问详情
          - provoke/challenge/threaten → 警戒应对
          - gift/deliver_msg → 接受并道谢

        失败时不影响主流程，返回空字符串。
        """
        eng = self.engine
        if not eng.npc_agent or not eng.player_state:
            return ""

        # 找到对应 NPC
        npc_id = event.source_npc
        npc = eng.npc_states.get(npc_id) if eng.npc_states else None
        if not npc:
            return ""

        # 根据事件类型推断主角的应对行动
        action_map = {
            "visit": "你前来拜访，我开门相迎，寒暄几句。",
            "invite": "你邀我同往，我询问去往何处、所为何事。",
            "gift": "你带来礼物，我欣然接受并道谢。",
            "chat": "你路过门前，我请你进来叙旧喝茶。",
            "greet": "你路过打招呼，我点头致意，闲聊几句。",
            "ask_help": "你神色焦急需要帮助，我询问发生了什么事。",
            "deliver_msg": "你带来消息，我仔细倾听并询问详情。",
            "provoke": "你上门挑衅，我冷脸相对，不卑不亢。",
            "challenge": "你送来战书，我接下并约定时辰。",
            "threaten": "你登门威胁，我冷笑不退让。",
            "cold_visit": "你顺道拜访，我客气应酬几句。",
        }
        player_action = action_map.get(event.event_type, "我与对方交谈。")

        try:
            result = eng.npc_agent.interact_with_player(
                npc, eng.player_state, player_action, eng.world_state
            )
            dialogue = result.get("dialogue", "").strip()
            favor_change = result.get("favor_change", 0)

            # 拼接对话片段
            fragment = f"【{npc.name}】{dialogue}"
            if favor_change:
                sign = "+" if favor_change > 0 else ""
                fragment += f"（好感{sign}{favor_change}）"
            logger.debug("[AutoRun] 代演对话: %s ← %s", npc.name, event.event_type)
            return fragment
        except Exception as e:
            logger.warning("[AutoRun] 代演对话失败 (%s): %s", npc.name, e)
            return ""

    # ===== 日志格式化 =====

    def _format_day_log(self, day: int, player_events, world_events,
                        npc_events, sleeping_events, war_events,
                        interactions, reactions=None) -> str:
        """把一天的事件拼成结构化文本，供最终章节生成 LLM 使用。"""
        eng = self.engine
        parts = []
        reactions = reactions or []

        # 日期 + 天气 + 季节
        weather = getattr(eng.world_state, "weather", "")
        season = getattr(eng.world_state, "season", "")
        time_str = getattr(eng.world_state, "current_time", "")
        parts.append(f"=== 第{day}天 {time_str} {season} {weather} ===")

        # 主角状态摘要
        if eng.player_state:
            ps = eng.player_state
            loc = getattr(ps, "location", "")
            health = getattr(ps.stats, "health", 0) if ps.stats else 0
            energy = getattr(ps.stats, "energy", 0) if ps.stats else 0
            mood = "、".join(ps.status_effects) if ps.status_effects else "正常"
            parts.append(f"主角{ps.name}（{ps.age}岁）于{loc}，气血{health} 体力{energy}，状态：{mood}")

        # NPC 主动接触事件
        if player_events:
            parts.append("【来客】")
            for evt in player_events:
                parts.append(f"  - {evt.title}")

        # 代演对话
        if interactions:
            parts.append("【交谈】")
            for it in interactions:
                parts.append(f"  {it['npc']}（{it['event_type']}）: {it['dialogue']}")

        # 世界事件
        if world_events:
            parts.append("【天下事】")
            for evt in world_events:
                parts.append(f"  - {evt.title}：{evt.summary}")

        # NPC 演化事件（涉及主角的优先）
        if npc_events:
            parts.append("【众人】")
            for ev in npc_events[:8]:  # 限制条数避免日志过长
                name = ev.get("npc_name") or ev.get("npc_id") or "某人"
                action = ev.get("action") or ev.get("detail") or ""
                location = ev.get("location", "")
                loc_suffix = f"（于{location}）" if location else ""
                parts.append(f"  - {name}{loc_suffix}: {action}")

        # [v1.2] NPC 即时反应（同场 NPC 感知到事件后的反应）
        if reactions:
            parts.append("【旁观】")
            for r in reactions:
                parts.append(f"  - {r['npc_name']}: {r['detail']}")

        # 睡眠事件（NPC 夜间活动）
        if sleeping_events:
            parts.append("【夜间】")
            for ev in sleeping_events[:4]:
                detail = ev.get("detail") or ev.get("description") or ""
                parts.append(f"  - {detail}")

        # 战争事件
        if war_events:
            parts.append("【战事】")
            for ev in war_events[:4]:
                desc = ev.get("description") or ev.get("detail") or str(ev)
                parts.append(f"  - {desc}")

        # 若全天无事
        if (not player_events and not world_events and not npc_events
                and not interactions and not reactions):
            parts.append("（今日平静无事）")

        return "\n".join(parts)

    # ===== 章节汇总 =====

    def _generate_summary_chapter(self, daily_logs: list[str],
                                  from_day: int, to_day: int) -> tuple[str, bool]:
        """把多日日志汇总成完整小说章节。

        复用 NarrativeEngine.generate_novel_chapter，传入拼接后的 full_log。
        与 GameEngine.generate_novel_chapter 不同：
          - 不依赖 last_novel_checkpoint 切片，而是直接用自主运行期间累积的日志
          - 不更新 last_novel_checkpoint（避免影响正常游戏的章节切分逻辑）
          - 章节文件单独保存，文件名含 autorun 标识

        返回: (chapter_text, llm_failed)
          - llm_failed=True 表示 LLM 章节生成失败，已降级为日志摘要
        """
        eng = self.engine
        if not eng.narrative or not eng.player_state or not eng.world_state:
            return "\n\n".join(daily_logs), True  # 无叙事引擎，视为降级

        full_log = "\n\n".join(daily_logs)

        # 拼接蝴蝶效应信息（小说模式下有意义）
        butterfly_info = ""
        if eng.butterfly:
            try:
                butterfly_info = eng.butterfly.get_world_memory() or ""
            except Exception:
                butterfly_info = ""

        # 经济信息
        economy_info = ""
        if eng.world_state.economy and eng.economy_system:
            try:
                economy_info = eng.economy_system.get_market_report(eng.world_state.economy)
            except Exception:
                economy_info = ""

        age_info = f"当前年龄: {eng.player_state.age}岁"

        # 在 full_log 头部加上时间范围说明，让 LLM 知道这是跨日汇总
        days_span = to_day - from_day
        header = f"【自主运行记录 · 第{from_day}天 ~ 第{to_day}天 · 共{days_span}天】\n\n"
        full_log_with_header = header + full_log

        # [v1.2] 多日汇总时放大日志预算和输出 token
        # 单日 log_budget=3000 / max_tokens=1500（约 750 中文字）
        # 多日 log_budget=6000 / max_tokens=4000（支持 2000-3000 中文字章节）
        # 日志预算按天数线性增长，但上限 8000（避免 prompt 过长）
        multi_day_log_budget = min(8000, 3000 + days_span * 500)
        multi_day_max_tokens = 4000 if days_span > 1 else 1500

        chapter = eng.narrative.generate_novel_chapter(
            eng.player_state, eng.world_state, full_log_with_header,
            age_info=age_info,
            economy_info=economy_info,
            butterfly_info=butterfly_info,
            max_tokens=multi_day_max_tokens,
            log_budget=multi_day_log_budget,
            days_span=days_span,
        )

        # [Bug] 检测 LLM 是否返回了 fallback 兜底文案。
        # router.chat 在所有真实模型失败时会返回 RuleBasedFallbackLLM 的 JSON 文案
        # （形如 {"narrative": "...AI服务暂时繁忙...", "options": [...]}）。
        # 这种文案是给游戏回合用的，不是小说章节；若直接保存会让用户看到一段
        # 错误提示却以为生成了章节。检测到时降级为原始日志拼接，并标记失败。
        llm_failed = self._is_fallback_chapter(chapter)
        if llm_failed:
            logger.warning(
                "[AutoRun] 章节生成 LLM 失败（返回兜底文案），降级为日志摘要"
            )
            chapter = self._build_log_summary_chapter(
                daily_logs, from_day, to_day
            )

        # 保存到独立文件（不影响 last_novel_checkpoint）
        try:
            self._save_autorun_chapter(
                chapter, from_day, to_day, daily_logs,
                llm_failed=llm_failed,
            )
        except Exception as e:
            logger.warning("[AutoRun] 章节文件保存失败: %s", e)

        return chapter, llm_failed

    def _is_fallback_chapter(self, chapter: str) -> bool:
        """检测章节内容是否为 LLM 路由器的 fallback 兜底文案。"""
        if not chapter or not isinstance(chapter, str):
            return True
        # fallback 文案是 JSON 字符串，且包含特征文本
        fallback_markers = (
            "AI服务暂时繁忙",
            "世界仿佛停滞了片刻",
        )
        for marker in fallback_markers:
            if marker in chapter:
                return True
        return False

    def _build_log_summary_chapter(self, daily_logs: list[str],
                                   from_day: int, to_day: int) -> str:
        """LLM 章节生成失败时的兜底：把运行日志整理为可读的章节摘要。"""
        header = (
            f"【自主运行章节 · 第{from_day}天 ~ 第{to_day}天】\n\n"
            "（注：章节小说化生成失败，以下为事件日志摘要，可稍后重试生成。）\n\n"
        )
        return header + "\n\n".join(daily_logs)

    def _save_autorun_chapter(self, chapter: str, from_day: int, to_day: int,
                              daily_logs: list[str], llm_failed: bool = False):
        """保存自主运行章节到 saves/{world_id}/narrative/chapter_autorun_*.json"""
        from datetime import datetime
        eng = self.engine
        if not eng.save_manager or not eng.current_world_id:
            return

        narrative_dir = eng.save_manager.base_dir / eng.current_world_id / "narrative"
        narrative_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        chapter_file = narrative_dir / f"chapter_autorun_{stamp}.json"

        chapter_data = {
            "type": "autorun_chapter",
            "chapter": chapter,
            "from_day": from_day,
            "to_day": to_day,
            "days": to_day - from_day,
            "daily_logs": daily_logs,
            "created_at": stamp,
            "llm_failed": llm_failed,  # [Bug] 标记章节是否为 LLM 失败后的日志兜底
        }
        eng.save_manager._write_json(chapter_file, chapter_data)
        logger.info("[AutoRun] 章节已保存: %s", chapter_file.name)
