"""
[v1.3] MemoryBriefManager — 统一多md记忆骨架

核心思路（融合 Hermes Agent 常驻摘要 + MIRIX 分层 + Generative Agents 反思）：
  1. 维护 5 个常驻 md 文件，作为 LLM system prompt 的"长期记忆常驻层"
  2. 每个文件控制 1000-1200 字符，总和 5000-6000 字符
  3. 双触发更新机制：
     - 异步增量：每 N 回合（默认 8）刷新一次活跃文件
     - 睡眠巩固：玩家"过夜"时全量重写所有 md
  4. 修复 Memory Curator 断层：brief 文本通过 get_briefs_for_prompt() 注入到 fixed_prompt

5 个 md 文件：
  - world_brief.md      世界观常驻摘要（地理/政治/时代/关键设定）
  - npc_dossiers.md     NPC 档案分段（每位核心 NPC 一段）
  - player_profile.md   玩家画像（身份/目标/习惯/偏好/重要决策）
  - active_threads.md   活跃剧情线索（伏笔/未解事件/任务进度）
  - meta_memory.md      元记忆索引（已遗忘/已归档/检索指南）

存储位置：saves/{world_id}/briefs/
"""
from __future__ import annotations
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .llm.base_llm import BaseLLM
    from .game_engine import GameEngine

logger = logging.getLogger("chronoverse.brief")


# ── 单文件字符预算 ──────────────────────────────────────
BRIEF_BUDGET = {
    "world_brief.md": 1200,
    "npc_dossiers.md": 1500,
    "player_profile.md": 1000,
    "active_threads.md": 1200,
    "meta_memory.md": 800,
}
TOTAL_BUDGET = sum(BRIEF_BUDGET.values())  # 5700

# 触发间隔
INCREMENTAL_INTERVAL = 8  # 每 8 回合触发一次增量更新


class MemoryBriefManager:
    """
    [v1.3] 统一记忆骨架管理器

    维护 5 个 md 文件作为 LLM 的常驻记忆层，
    并提供 get_briefs_for_prompt() 注入到 fixed_prompt。
    """

    def __init__(self, llm: "BaseLLM | None" = None, saves_dir: str = "./saves"):
        self.llm = llm
        self.saves_dir = Path(saves_dir)
        self.current_world_id: str = ""
        self.briefs_dir: Path = self.saves_dir  # 默认占位，set_world_id 时切换
        self._cache: dict[str, str] = {}  # 文件名 → 内容缓存
        self._last_incremental_turn: int = 0
        self._last_sleep_day: int = -1  # 上次睡眠巩固的游戏日
        self._update_count: int = 0
        self._lock = threading.Lock()
        # 标记是否已加载过磁盘缓存
        self._loaded: bool = False

    # ── 世界切换 ──────────────────────────────────────────
    def set_world_id(self, world_id: str):
        """切换当前世界，加载对应的 briefs 目录。"""
        self.current_world_id = world_id or ""
        if world_id:
            self.briefs_dir = self.saves_dir / world_id / "briefs"
        else:
            self.briefs_dir = self.saves_dir / "briefs"
        try:
            self.briefs_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self._cache.clear()
        self._loaded = False
        self._load_from_disk()

    def set_llm(self, llm: "BaseLLM"):
        """延迟注入 LLM（GameEngine._init_services 完成后调用）。"""
        self.llm = llm

    # ── 磁盘 IO ──────────────────────────────────────────
    def _load_from_disk(self):
        """从磁盘加载所有 md 文件到缓存。"""
        if not self.briefs_dir.exists():
            self._loaded = True
            return
        for fname in BRIEF_BUDGET:
            fp = self.briefs_dir / fname
            if fp.exists():
                try:
                    self._cache[fname] = fp.read_text(encoding="utf-8")
                except Exception as e:
                    logger.warning("Failed to load brief %s: %s", fname, e)
                    self._cache[fname] = ""
            else:
                self._cache[fname] = ""
        self._loaded = True

    def _write_brief(self, fname: str, content: str):
        """原子写入单个 md 文件。"""
        try:
            self.briefs_dir.mkdir(parents=True, exist_ok=True)
            fp = self.briefs_dir / fname
            fp.write_text(content, encoding="utf-8")
            self._cache[fname] = content
        except Exception as e:
            logger.warning("Failed to write brief %s: %s", fname, e)

    def _truncate_to_budget(self, content: str, fname: str) -> str:
        """按字符预算截断，保留完整段落。"""
        budget = BRIEF_BUDGET.get(fname, 1000)
        if len(content) <= budget:
            return content
        # 在预算内找最后一个换行符，保留完整段落
        cut = content.rfind("\n", 0, budget)
        if cut < budget * 0.5:
            cut = budget
        return content[:cut].rstrip() + "\n（已截断）"

    # ── Prompt 注入接口 ─────────────────────────────────
    def get_briefs_for_prompt(self) -> str:
        """
        返回整合后的常驻摘要文本，供 turn_processor 注入到 fixed_prompt。
        修复 Memory Curator 断层：这是 brief 实际进入 prompt 的唯一通道。
        """
        if not self._loaded:
            self._load_from_disk()

        parts = ["【长期记忆常驻层 · MemoryBriefs】"]
        any_content = False

        # 世界观摘要
        wb = self._cache.get("world_brief.md", "").strip()
        if wb:
            parts.append(f"\n— 世界观摘要 —\n{wb}")
            any_content = True

        # NPC 档案
        nd = self._cache.get("npc_dossiers.md", "").strip()
        if nd:
            parts.append(f"\n— 核心NPC档案 —\n{nd}")
            any_content = True

        # 玩家画像
        pp = self._cache.get("player_profile.md", "").strip()
        if pp:
            parts.append(f"\n— 玩家画像 —\n{pp}")
            any_content = True

        # 活跃线索
        at = self._cache.get("active_threads.md", "").strip()
        if at:
            parts.append(f"\n— 活跃剧情线索 —\n{at}")
            any_content = True

        # 元记忆索引
        mm = self._cache.get("meta_memory.md", "").strip()
        if mm:
            parts.append(f"\n— 元记忆索引 —\n{mm}")
            any_content = True

        if not any_content:
            return ""

        return "\n".join(parts)

    # ── 触发判断 ────────────────────────────────────────
    def should_incremental_update(self, current_turn: int) -> bool:
        """是否应该触发增量更新"""
        return (current_turn - self._last_incremental_turn) >= INCREMENTAL_INTERVAL

    def should_consolidate_on_sleep(self, current_day: int, time_signal: str = "") -> bool:
        """
        判断是否触发睡眠巩固。
        触发条件：游戏日推进到新的一天 + 时间信号暗示"过夜"（夜晚→清晨）
        """
        if current_day > self._last_sleep_day:
            # 简单触发条件：日期变更即可
            # 进阶条件可检查 time_signal 是否含"清晨/早晨/天亮"等关键词
            return True
        return False

    # ── 增量更新 ────────────────────────────────────────
    def update_briefs_incremental(self, engine: "GameEngine", current_turn: int) -> dict:
        """
        异步增量更新：仅刷新 active_threads.md + player_profile.md
        这两个文件变化最频繁，且不依赖 LLM 全量重写。
        """
        if not self.llm:
            return {"status": "skipped", "reason": "no llm"}

        with self._lock:
            self._last_incremental_turn = current_turn
            self._update_count += 1

            report = {"updated": [], "skipped": []}

            # 1. 更新活跃线索（基于 foreshadow_lifecycle + 最近叙事）
            try:
                at_content = self._build_active_threads(engine)
                if at_content:
                    at_content = self._truncate_to_budget(at_content, "active_threads.md")
                    self._write_brief("active_threads.md", at_content)
                    report["updated"].append("active_threads.md")
            except Exception as e:
                logger.warning("Incremental active_threads failed: %s", e)
                report["skipped"].append("active_threads.md")

            # 2. 更新玩家画像（基于最近 10 回合行为）
            try:
                pp_content = self._build_player_profile(engine)
                if pp_content:
                    pp_content = self._truncate_to_budget(pp_content, "player_profile.md")
                    self._write_brief("player_profile.md", pp_content)
                    report["updated"].append("player_profile.md")
            except Exception as e:
                logger.warning("Incremental player_profile failed: %s", e)
                report["skipped"].append("player_profile.md")

            logger.info("Brief incremental update #%d: %s",
                        self._update_count, report)
            return report

    # ── 睡眠巩固（全量重写） ───────────────────────────
    def consolidate_on_sleep(self, engine: "GameEngine", current_day: int) -> dict:
        """
        玩家"过夜"时触发，全量重写所有 5 个 md 文件。
        调用 LLM 整合 memory_curator 的历史摘要 + 最近叙事。
        """
        if not self.llm:
            return {"status": "skipped", "reason": "no llm"}

        with self._lock:
            self._last_sleep_day = current_day
            self._update_count += 1

            report = {"updated": [], "skipped": [], "day": current_day}

            # 1. 世界观摘要（全量重写）
            try:
                wb = self._build_world_brief(engine)
                if wb:
                    wb = self._truncate_to_budget(wb, "world_brief.md")
                    self._write_brief("world_brief.md", wb)
                    report["updated"].append("world_brief.md")
            except Exception as e:
                logger.warning("Sleep consolidate world_brief failed: %s", e)
                report["skipped"].append("world_brief.md")

            # 2. NPC 档案（全量重写）
            try:
                nd = self._build_npc_dossiers(engine)
                if nd:
                    nd = self._truncate_to_budget(nd, "npc_dossiers.md")
                    self._write_brief("npc_dossiers.md", nd)
                    report["updated"].append("npc_dossiers.md")
            except Exception as e:
                logger.warning("Sleep consolidate npc_dossiers failed: %s", e)
                report["skipped"].append("npc_dossiers.md")

            # 3. 玩家画像（全量重写）
            try:
                pp = self._build_player_profile(engine)
                if pp:
                    pp = self._truncate_to_budget(pp, "player_profile.md")
                    self._write_brief("player_profile.md", pp)
                    report["updated"].append("player_profile.md")
            except Exception as e:
                logger.warning("Sleep consolidate player_profile failed: %s", e)
                report["skipped"].append("player_profile.md")

            # 4. 活跃线索（全量重写）
            try:
                at = self._build_active_threads(engine)
                if at:
                    at = self._truncate_to_budget(at, "active_threads.md")
                    self._write_brief("active_threads.md", at)
                    report["updated"].append("active_threads.md")
            except Exception as e:
                logger.warning("Sleep consolidate active_threads failed: %s", e)
                report["skipped"].append("active_threads.md")

            # 5. 元记忆索引（全量重写）
            try:
                mm = self._build_meta_memory(engine)
                if mm:
                    mm = self._truncate_to_budget(mm, "meta_memory.md")
                    self._write_brief("meta_memory.md", mm)
                    report["updated"].append("meta_memory.md")
            except Exception as e:
                logger.warning("Sleep consolidate meta_memory failed: %s", e)
                report["skipped"].append("meta_memory.md")

            logger.info("Brief sleep consolidate on day %d: %s", current_day, report)
            return report

    # ── 5 个 md 构建方法 ────────────────────────────────
    def _build_world_brief(self, engine: "GameEngine") -> str:
        """构建世界观摘要：地理/政治/时代/关键设定"""
        ws = engine.world_state
        wd = engine.world_def or {}
        if not ws:
            return ""

        # 已有缓存 → 增量补全；否则首次构建
        existing = self._cache.get("world_brief.md", "")

        # 收集原始素材
        materials = []
        materials.append(f"世界名：{ws.world_name}")
        materials.append(f"类型：{ws.world_type}")
        if ws.era_name:
            materials.append(f"时代：{ws.era_name} {ws.era_year}年")
        if ws.description:
            materials.append(f"简介：{ws.description[:200]}")
        if wd.get("locations"):
            locs = wd["locations"]
            if isinstance(locs, dict):
                loc_summary = "；".join(
                    f"{k}({v.get('type', '?')})" if isinstance(v, dict) else str(k)
                    for k, v in list(locs.items())[:8]
                )
                materials.append(f"主要地点：{loc_summary}")
        materials.append(f"当前游戏日：第{ws.current_day}天")

        # 注入 memory_curator 的历史摘要
        if engine.memory_curator and engine.memory_curator._history_summaries:
            summaries_text = "\n".join(
                s.get("text", "")[:300]
                for s in engine.memory_curator._history_summaries[-3:]
            )
            if summaries_text:
                materials.append(f"近期剧情摘要：\n{summaries_text}")

        # 调用 LLM 整合
        prompt = f"""你是世界观记录官，请将以下素材整合成一段紧凑的世界观摘要（800-1200字）。

【素材】
{chr(10).join(materials)}

【已存在的旧摘要（参考，可保留关键信息）】
{existing}

【要求】
1. 包含：世界设定、时代背景、关键地理、当前剧情态势
2. 用第三人称叙述，简洁有力
3. 不要重复 player 信息或 npc 个人细节（那些在其他文件）
4. 直接输出摘要内容，不要加标题或前言"""

        try:
            result = self.llm.chat(prompt, temperature=0.4, max_tokens=1500)
            return result.strip()
        except Exception as e:
            logger.warning("LLM world_brief failed: %s", e)
            # 回退：用素材拼简单摘要
            return "\n".join(materials)

    def _build_npc_dossiers(self, engine: "GameEngine") -> str:
        """构建 NPC 档案：每位核心 NPC 一段（人设+关系+近期动态）"""
        if not engine.npc_states:
            return ""

        # 筛选核心 NPC：与玩家有过交互的（is_dormant=False，且有 impression_of_player 或关系）
        core_npcs = []
        for npc_id, npc in engine.npc_states.items():
            if getattr(npc, "is_dormant", False):
                continue
            # 优先选有印象或关系的 NPC
            impressions = getattr(npc, "impression_of_player", []) or []
            relations = getattr(npc, "relation_to_player", {}) or {}
            if impressions or relations or len(core_npcs) < 5:
                core_npcs.append((npc_id, npc))
            if len(core_npcs) >= 8:
                break

        if not core_npcs:
            return ""

        # 构建 NPC 信息块
        npc_blocks = []
        for npc_id, npc in core_npcs:
            block_parts = [f"【{npc.name}】"]
            if getattr(npc, "role", ""):
                block_parts.append(f"身份：{npc.role}")
            if getattr(npc, "age", 0):
                block_parts.append(f"年龄：{npc.age}")
            if getattr(npc, "personality", ""):
                block_parts.append(f"性格：{npc.personality[:80]}")
            if getattr(npc, "speaking_style", ""):
                block_parts.append(f"说话风格：{npc.speaking_style[:60]}")
            # 关系
            rel = getattr(npc, "relation_to_player", {}) or {}
            if isinstance(rel, dict) and rel.get("relation_type"):
                block_parts.append(f"与玩家关系：{rel['relation_type']}")
                if rel.get("favorability"):
                    block_parts.append(f"好感度：{rel['favorability']}")
            # 印象（最近3条）
            impressions = getattr(npc, "impression_of_player", []) or []
            if impressions:
                recent_imp = impressions[-3:]
                imp_text = "；".join(
                    imp.get("text", str(imp))[:60] if isinstance(imp, dict) else str(imp)[:60]
                    for imp in recent_imp
                )
                block_parts.append(f"对玩家印象：{imp_text}")
            # 私密事实（限2条，避免泄露过多秘密）
            pf = getattr(npc, "private_facts", []) or []
            if pf:
                facts_text = "；".join(str(f)[:50] for f in pf[:2])
                block_parts.append(f"私密事实：{facts_text}")
            # 当前位置
            loc = getattr(npc, "current_location", "")
            if loc:
                block_parts.append(f"当前位置：{loc}")

            npc_blocks.append("\n".join(block_parts))

        materials = "\n\n".join(npc_blocks)

        # LLM 精炼
        prompt = f"""你是 NPC 档案官，请将以下 NPC 信息整合成档案分段（总计 1000-1500字）。

【NPC原始信息】
{materials}

【要求】
1. 每位 NPC 一段，标题用【NPC名】
2. 每段包含：身份/性格/与玩家关系/近期印象/位置
3. 保持简洁，不要编造新信息
4. 直接输出档案内容"""

        try:
            result = self.llm.chat(prompt, temperature=0.3, max_tokens=2000)
            return result.strip()
        except Exception as e:
            logger.warning("LLM npc_dossiers failed: %s", e)
            return materials

    def _build_player_profile(self, engine: "GameEngine") -> str:
        """构建玩家画像：身份/目标/习惯/偏好/重要决策"""
        ps = engine.player_state
        if not ps:
            return ""

        materials = []
        materials.append(f"姓名：{ps.name}")
        materials.append(f"年龄：{ps.age}岁")
        if ps.social and ps.social.position:
            materials.append(f"身份：{ps.social.position}")
        if ps.tags:
            materials.append(f"标签：{'、'.join(ps.tags[:8])}")
        if ps.current_goal:
            materials.append(f"当前目标：{ps.current_goal}")
        if ps.stats:
            try:
                stats_text = ", ".join(
                    f"{k}:{v}" for k, v in ps.stats.model_dump().items()
                    if v and v != 0
                )
                if stats_text:
                    materials.append(f"能力：{stats_text[:200]}")
            except Exception:
                pass
        if ps.memory and ps.memory.short_term:
            materials.append(f"近期行为：\n" + "\n".join(ps.memory.short_term[-8:]))

        # 提取玩家近期输入（从 narrative_history 中 type==player 的条目）
        player_inputs = []
        for entry in (engine.narrative_history or [])[-20:]:
            if entry.get("type") == "player" or entry.get("is_player_input"):
                text = entry.get("text", "") or entry.get("content", "")
                if text:
                    player_inputs.append(text[:80])
        if player_inputs:
            materials.append(f"近期玩家输入：\n" + "\n".join(player_inputs[-6:]))

        existing = self._cache.get("player_profile.md", "")

        prompt = f"""你是玩家行为分析师，请整合以下素材为玩家画像（700-1000字）。

【素材】
{chr(10).join(materials)}

【已有旧画像（参考）】
{existing}

【要求】
1. 包含：身份/目标/能力/行为习惯/决策偏好/社交风格
2. 重点刻画"玩家偏好"（如：偏好战斗/对话/探索/谋略）
3. 用第三人称叙述
4. 直接输出画像内容"""

        try:
            result = self.llm.chat(prompt, temperature=0.4, max_tokens=1500)
            return result.strip()
        except Exception as e:
            logger.warning("LLM player_profile failed: %s", e)
            return "\n".join(materials)

    def _build_active_threads(self, engine: "GameEngine") -> str:
        """构建活跃剧情线索：伏笔/未解事件/任务进度"""
        materials = []

        # 1. 从 foreshadow_lifecycle 获取活跃伏笔
        if engine.foreshadow_lifecycle:
            try:
                hooks = engine.foreshadow_lifecycle.get_hooks_for_prompt(max_hooks=8)
                if hooks:
                    materials.append(hooks)
            except Exception as e:
                logger.debug("foreshadow hooks failed: %s", e)

        # 2. 从 quest_system 获取活跃任务
        if engine.quest_system:
            try:
                quests = engine.quest_system.get_active_quests() if hasattr(engine.quest_system, "get_active_quests") else []
                if quests:
                    quest_text = "\n".join(
                        f"- {q.get('name', '?')}: {q.get('description', '')[:80]}"
                        for q in quests[:5]
                    )
                    materials.append(f"【活跃任务】\n{quest_text}")
            except Exception as e:
                logger.debug("quest system failed: %s", e)

        # 3. 从 narrative_history 提取近期"未解决"事件
        recent_events = []
        for entry in (engine.narrative_history or [])[-15:]:
            etype = entry.get("type", "")
            text = entry.get("text", "")
            if etype == "event" and text:
                recent_events.append(text[:100])
        if recent_events:
            materials.append(f"【近期事件】\n" + "\n".join(f"- {e}" for e in recent_events[-5:]))

        # 4. CausalGraph 中的高重要性节点
        if engine.causal_graph and engine.causal_graph.nodes:
            try:
                top_nodes = sorted(
                    engine.causal_graph.nodes,
                    key=lambda n: n.get("importance", 0),
                    reverse=True
                )[:5]
                if top_nodes:
                    cg_text = "\n".join(
                        f"- 第{n.get('day', '?')}天: {n.get('effects_summary', n.get('narrative_excerpt', ''))[:80]}"
                        for n in top_nodes
                    )
                    materials.append(f"【关键因果节点】\n{cg_text}")
            except Exception as e:
                logger.debug("causal graph extract failed: %s", e)

        if not materials:
            return ""

        joined = "\n\n".join(materials)

        # LLM 整合
        prompt = f"""你是剧情线索整理官，请整合以下素材为活跃剧情线索摘要（800-1200字）。

【素材】
{joined}

【要求】
1. 分类列出：未回收伏笔 / 进行中任务 / 近期关键事件 / 待解决冲突
2. 标注每个线索的紧迫程度（高/中/低）
3. 用简洁列表形式，便于 LLM 快速理解
4. 直接输出线索内容"""

        try:
            result = self.llm.chat(prompt, temperature=0.3, max_tokens=1800)
            return result.strip()
        except Exception as e:
            logger.warning("LLM active_threads failed: %s", e)
            return joined

    def _build_meta_memory(self, engine: "GameEngine") -> str:
        """构建元记忆索引：记忆统计/检索指南/已遗忘条目"""
        materials = []

        # 1. Memory Curator 统计
        if engine.memory_curator:
            stats = engine.memory_curator.get_curate_stats()
            materials.append(
                f"记忆整理次数：{stats.get('total_curations', 0)}；"
                f"归档记忆条数：{stats.get('archived_memories', 0)}"
            )
            summaries = engine.memory_curator._history_summaries
            if summaries:
                materials.append(
                    f"历史摘要条数：{len(summaries)}；"
                    f"覆盖天数：{summaries[0].get('day_range', [0])[0]}-"
                    f"{summaries[-1].get('day_range', [0])[-1]}"
                )

        # 2. ChromaDB 记忆条数
        if engine.memory:
            try:
                all_mems = engine.memory.collection.get()
                if all_mems and all_mems.get("ids"):
                    materials.append(f"向量记忆库条目数：{len(all_mems['ids'])}")
            except Exception as e:
                logger.debug("chroma stats failed: %s", e)

        # 3. GraphRAG 统计
        if engine.graph_rag:
            try:
                entities = engine.graph_rag.entities
                relations = engine.graph_rag.relations
                materials.append(f"图谱实体数：{len(entities)}；关系数：{len(relations)}")
            except Exception as e:
                logger.debug("graphrag stats failed: %s", e)

        # 4. 检索指南
        materials.append(
            "检索指南：日常场景优先 BM25；重要剧情用 GraphRAG；"
            "玩家偏好查询用向量；伏笔回收看 active_threads"
        )

        if not materials:
            return ""

        joined = "\n".join(materials)

        # 元记忆不需要 LLM 重写，直接结构化输出
        return joined

    # ── 序列化（用于 game_state.json 持久化） ─────────
    def to_dict(self) -> dict:
        return {
            "last_incremental_turn": self._last_incremental_turn,
            "last_sleep_day": self._last_sleep_day,
            "update_count": self._update_count,
            "current_world_id": self.current_world_id,
        }

    def from_dict(self, data: dict):
        self._last_incremental_turn = data.get("last_incremental_turn", 0)
        self._last_sleep_day = data.get("last_sleep_day", -1)
        self._update_count = data.get("update_count", 0)

    def get_stats(self) -> dict:
        return {
            "update_count": self._update_count,
            "last_incremental_turn": self._last_incremental_turn,
            "last_sleep_day": self._last_sleep_day,
            "briefs_loaded": len([v for v in self._cache.values() if v]),
            "total_chars": sum(len(v) for v in self._cache.values()),
        }
