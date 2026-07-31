"""
[v12] 小说人物扮演服务层 — 串联知识库、时间轴、记忆系统。

职责：
1. 导入小说：分块 → 建图谱 → 构建时间轴
2. 角色提取：从图谱中提取主要角色
3. 进入游戏：注入原著记忆 + 关系图谱 + 设置分歧点

这是路由层和底层模块之间的业务编排层。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .game_engine import GameEngine
    from .llm.base_llm import BaseLLM

logger = logging.getLogger("chronoverse.novel_roleplay")


class NovelRoleplayService:
    """
    [v12] 小说人物扮演服务。

    管理一次"小说人物扮演"的完整生命周期：
    导入小说 → 角色选择 → 时间轴选择 → 进入游戏
    """

    def __init__(self, llm: "BaseLLM" = None,
                 embedding_func=None,
                 storage_dir: str = None):
        self.llm = llm
        self.embedding_func = embedding_func
        self.storage_dir = storage_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "data", "novel_roleplay"
        )
        os.makedirs(self.storage_dir, exist_ok=True)

        # 三个核心组件
        from .novel_knowledge_base import NovelKnowledgeBase
        from .timeline_engine import TimelineEngine
        from .chunker import SemanticChunker

        self.knowledge_base = NovelKnowledgeBase(
            llm=llm,
            embedding_func=embedding_func,
            storage_dir=os.path.join(self.storage_dir, "kb"),
        )
        self.timeline = TimelineEngine()
        self.chunker = SemanticChunker()

        # 导入状态追踪
        self._import_status: dict = {
            "state": "idle",        # idle/processing/done/error
            "progress": 0,          # 0-100
            "message": "",
            "novel_name": "",
            "total_chars": 0,
            "chunks": 0,
            "entities": 0,
            "relations": 0,
            "key_events": 0,
            "error": "",
        }
        self._novel_text: str = ""
        self._chunks: list = []

        # 打印 LLM/embedding 状态，便于诊断"0实体"问题
        llm_info = "无" if self.llm is None else f"{type(self.llm).__name__}"
        emb_info = "无" if embedding_func is None else f"{type(embedding_func).__name__}"
        logger.info("NovelRoleplayService 初始化: LLM=%s, Embedding=%s",
                    llm_info, emb_info)
        if self.llm is None:
            logger.warning(
                "⚠️ LLM 未配置！小说导入将无法提取角色。"
                "请确保 config.json 中 llm.api_key/base_url/model_name 已正确设置，"
                "或在主页先创建游戏世界以初始化 engine.main_llm。"
            )

    # ── 导入小说 ──────────────────────────────────────────

    async def import_novel(self, text: str, novel_name: str = "") -> dict:
        """
        [v12+] 快速导入：只做分块+章节扫描+1次LLM找角色。
        用户选完章节后，再调用 deep_process_before_chapter 做深度处理。

        耗时：约30秒（vs 之前的5-10分钟）
        """
        self._novel_text = text
        self._import_status.update({
            "state": "processing",
            "progress": 5,
            "message": "开始智能分块...",
            "novel_name": novel_name or f"小说_{int(time.time())}",
            "total_chars": len(text),
            "error": "",
        })

        try:
            # 第一步：智能分块
            chunks = await asyncio.to_thread(self.chunker.chunk, text)
            self._chunks = chunks
            self._import_status.update({
                "progress": 20,
                "message": f"分块完成: {len(chunks)} 块，开始扫描章节...",
                "chunks": len(chunks),
            })

            # 第二步：快速导入（章节扫描+找角色，不做深度处理）
            def _progress_cb(msg: str):
                self._import_status["message"] = msg
                self._import_status["progress"] = min(90, self._import_status["progress"] + 3)

            kb_result = await self.knowledge_base.quick_ingest(
                text, progress_callback=_progress_cb, pre_chunked=chunks
            )

            self._import_status.update({
                "progress": 100,
                "state": "done",
                "message": "快速导入完成！请选择角色。",
                "entities": kb_result.get("entities", 0),
                "relations": 0,  # 快速导入不提取关系
                "chapters": kb_result.get("chapters", 0),
                "key_events": 0,  # 快速导入不构建时间轴
            })

            logger.info("快速导入完成: %d字, %d块, %d章节, %d角色",
                        len(text), len(chunks),
                        kb_result.get("chapters", 0),
                        kb_result.get("entities", 0))

            return self._import_status.copy()

        except Exception as e:
            logger.error("快速导入失败: %s", e, exc_info=True)
            self._import_status.update({
                "state": "error",
                "message": f"导入失败: {e}",
                "error": str(e),
            })
            return self._import_status.copy()

    async def deep_process_before_chapter(self, chapter_index: int = -1,
                                           character_name: str = "",
                                           char_position: int = -1) -> dict:
        """
        [v12+] 玩家选定章节后，深度处理该章节之前的内容。
        只提取选定章节前的实体/关系/时间线，章节之后的内容由玩家自由推演。

        参数（二选一）：
            chapter_index: 章节序号（0-based），处理 [0, chapter_index] 范围
            char_position: 直接指定字符位置（方案B/C用），处理 [0, char_position] 范围
            character_name: 玩家选定的角色名（用于聚焦关系提取）

        耗时：1-3分钟（取决于处理范围）
        """
        if not self._novel_text:
            return {"error": "尚未导入小说"}

        # 确定 char_end：优先 char_position，否则查 chapter_index
        if char_position is not None and char_position >= 0:
            char_end = min(char_position, len(self._novel_text))
            range_desc = f"前 {char_end} 字"
        else:
            if chapter_index < 0:
                return {"error": "必须提供 chapter_index 或 char_position"}
            chapters = await asyncio.to_thread(
                self.knowledge_base.get_chapters
            )
            if not chapters or chapter_index >= len(chapters):
                return {"error": f"章节序号 {chapter_index} 无效"}
            char_end = chapters[chapter_index]["char_end"]
            range_desc = f"前 {chapter_index + 1} 章"

        self._import_status.update({
            "state": "processing",
            "progress": 0,
            "message": f"开始深度处理{range_desc}内容...",
            "error": "",
        })

        try:
            # 截取到选定位置/章节末尾的文本
            text_to_process = self._novel_text[:char_end]

            def _progress_cb(msg: str):
                self._import_status["message"] = msg
                self._import_status["progress"] = min(90, self._import_status["progress"] + 3)

            # 深度处理：实体+关系+时间线
            # 过滤出 char_end 之前的 chunk（用首100字在全文中的位置判定）
            relevant_chunks = [
                c for c in self._chunks
                if hasattr(c, 'text') and c.text and
                self._novel_text.find(c.text[:100]) < char_end
            ]

            result = await self.knowledge_base.deep_ingest_partial(
                text_to_process,
                progress_callback=_progress_cb,
                existing_chunks=relevant_chunks,
                character_focus=character_name,
            )

            # 构建时间轴
            graph_rag = self.knowledge_base.get_graph_rag()
            if graph_rag and relevant_chunks:
                key_events = await asyncio.to_thread(
                    self.timeline.build_from_analysis,
                    relevant_chunks, graph_rag, 6.0
                )
                self._import_status["key_events"] = key_events

            self._import_status.update({
                "state": "done",
                "progress": 100,
                "message": "深度处理完成！即将进入游戏...",
                "entities": result.get("entities", 0),
                "relations": result.get("relations", 0),
            })

            logger.info("深度处理完成: %s, %d实体, %d关系",
                        range_desc,
                        result.get("entities", 0),
                        result.get("relations", 0))

            return self._import_status.copy()

        except Exception as e:
            logger.error("深度处理失败: %s", e, exc_info=True)
            self._import_status.update({
                "state": "error",
                "message": f"深度处理失败: {e}",
                "error": str(e),
            })
            return self._import_status.copy()

    def locate_text(self, snippet: str) -> dict:
        """
        [v12+] 在小说全文中查找玩家粘贴的文字位置。

        用于"粘贴文字定位"功能：玩家粘贴一段记得的小说文字，
        系统找到位置后，深度处理该位置之前的内容。

        参数：
            snippet: 玩家粘贴的文字片段（至少10字）

        返回：
            {
                "found": bool,
                "char_position": int,   # 文中位置（找不到为-1）
                "matched_text": str,    # 实际匹配的文字
                "progress_percent": float,  # 在全书中的进度百分比
                "error": str,           # 错误信息（如有）
            }
        """
        if not self._novel_text:
            return {"found": False, "char_position": -1, "error": "尚未导入小说"}

        if not snippet or len(snippet.strip()) < 20:
            return {"found": False, "char_position": -1,
                    "error": "请粘贴至少20字的小说文字"}

        # 清理：去掉首尾空白和多余换行
        snippet_clean = snippet.strip()

        # 直接查找
        pos = self._novel_text.find(snippet_clean)

        if pos == -1:
            # 尝试：去掉所有空白后匹配（应对换行/空格差异）
            import re
            snippet_compact = re.sub(r'\s+', '', snippet_clean)
            # 用滑动窗口在全文（也压缩空白）中找
            text_compact = re.sub(r'\s+', '', self._novel_text)
            pos_c = text_compact.find(snippet_compact)
            if pos_c != -1:
                # 反推回原文位置：粗略估算（压缩后位置 ≤ 原文位置）
                # 找到压缩文本中位置附近的字符，在原文中定位
                # 简化：用压缩前50字在原文中查找
                snippet_head = snippet_compact[:30]
                pos = self._novel_text.find(snippet_head)
                if pos == -1:
                    # 再尝试更短的片段
                    snippet_head = snippet_compact[:15]
                    pos = self._novel_text.find(snippet_head)

        if pos == -1:
            return {
                "found": False,
                "char_position": -1,
                "error": "未在小说中找到这段文字，请确认是否来自本小说",
            }

        progress_percent = (pos / len(self._novel_text)) * 100 if self._novel_text else 0
        # 返回匹配片段的前50字作为预览
        preview_end = min(pos + len(snippet_clean), pos + 80)
        matched = self._novel_text[pos:preview_end].replace('\n', ' ')

        logger.info("文字定位成功: 位置=%d, 进度=%.1f%%, 匹配片段=%s...",
                    pos, progress_percent, matched[:30])

        return {
            "found": True,
            "char_position": pos,
            "matched_text": matched,
            "progress_percent": round(progress_percent, 1),
            "total_chars": len(self._novel_text),
        }

    def get_import_status(self) -> dict:
        """获取导入进度"""
        return self._import_status.copy()

    # ── 角色选择 ──────────────────────────────────────────

    def get_chapters(self) -> list[dict]:
        """获取所有章节列表（用于章节选择页面）"""
        return self.knowledge_base.get_chapters()

    def get_characters(self, top_n: int = 12) -> list[dict]:
        """
        获取主要角色列表（按重要性排序）。
        用于角色选择界面。
        """
        characters = self.knowledge_base.get_main_characters(top_n=top_n)
        if not characters:
            return []

        # 为每个角色补充可用的起始时间点数
        for char in characters:
            char_name = char["name"]
            # 查找该角色出现的时间节点数
            available_points = 0
            for tid, node in self.timeline.nodes.items():
                if char_name in node.character_snapshots:
                    available_points += 1
            char["available_timeline_points"] = available_points

        return characters

    # ── 时间轴选择 ────────────────────────────────────────

    def get_key_events(self) -> list[dict]:
        """
        获取关键事件列表（供玩家选择时间节点）。
        """
        events = self.timeline.get_key_events(threshold=6.0)
        if not events:
            # 如果没有关键事件，返回所有节点的前20%
            all_events = self.timeline.get_timeline_summary()
            if all_events:
                cut = max(5, len(all_events) // 5)
                events = all_events[:cut]
        return events

    def get_timeline_summary(self) -> list[dict]:
        """获取完整时间轴摘要（用于前端可视化）"""
        return self.timeline.get_timeline_summary()

    # ── 进入游戏 ──────────────────────────────────────────

    async def enter_roleplay(self, character_name: str,
                              timeline_id: str,
                              engine: "GameEngine") -> dict:
        """
        玩家选择角色和时间点后，进入游戏。

        流程：
        1. 获取该时间点的状态快照
        2. 将时间点前的记忆注入 MemoryStore
        3. 将关系图谱注入 GraphRAG
        4. 构建世界数据，创建游戏
        5. 设置分歧标记（此后为自由推演）

        返回：游戏初始化结果
        """
        snapshot = self.timeline.get_snapshot(timeline_id)
        if not snapshot:
            return {"error": f"时间节点 {timeline_id} 不存在"}

        char_state = snapshot.character_snapshots.get(character_name)
        if not char_state:
            return {"error": f"角色 {character_name} 在时间点 {timeline_id} 不存在"}

        # 1. 获取该时间点前的所有累积记忆
        memories = self.timeline.get_memories_before(timeline_id)

        # 2. 注入原著事实到 MemoryStore
        if engine.memory:
            engine.memory.clear_novel_facts()
            for i, mem in enumerate(memories):
                engine.memory.add_novel_fact(
                    text=mem,
                    chapter=i,
                    entities=[character_name],
                    fact_type="event",
                    importance=0.8,
                )
            logger.info("注入 %d 条原著记忆", len(memories))

        # 3. 注入关系图谱到游戏内GraphRAG
        graph_rag = self.knowledge_base.get_graph_rag()
        if graph_rag and hasattr(engine, 'graph_rag') and engine.graph_rag:
            # [NovelRoleplay] 双层注入：明面关系（时间点前）+ 暗面关系（时间点后，标记 is_future）
            from copy import copy  # 深拷贝避免污染原图谱
            past_rel_count = 0
            future_rel_count = 0
            for rel in graph_rag.relations:
                # 深拷贝关系避免污染原图谱（copy 已在 if 块顶部导入）
                new_rel = copy(rel)
                if rel.turn > snapshot.chapter_index:
                    # 未来关系：标记 is_future，不进入活跃检索，但保留在图谱中
                    new_rel.is_future = True
                    future_rel_count += 1
                    engine.graph_rag.relations.append(new_rel)
                    continue
                # 只注入仍有效的关系
                if not rel.is_active:
                    continue
                new_rel.is_future = False
                past_rel_count += 1
                engine.graph_rag.relations.append(new_rel)
            # 注入实体（明面 + 暗面）
            past_entity_count = 0
            future_entity_count = 0
            for name, entity in graph_rag.entities.items():
                # 深拷贝实体（copy 已在 if 块顶部导入）
                new_entity = copy(entity)
                if entity.last_seen_turn > snapshot.chapter_index:
                    # 未来实体：标记 is_future，检索时降权
                    new_entity.is_future = True
                    future_entity_count += 1
                    engine.graph_rag.entities[name] = new_entity
                else:
                    new_entity.is_future = False
                    past_entity_count += 1
                    engine.graph_rag.entities[name] = new_entity
            logger.info("注入关系图谱: 明面 %d 实体/%d 关系, 暗面(未来) %d 实体/%d 关系",
                        past_entity_count, past_rel_count,
                        future_entity_count, future_rel_count)

        # 3.5 [NovelRoleplay] 注入未来角色为 dormant NPC + 未来事件为伏笔
        future_chars_injected = 0
        future_events_injected = 0
        if hasattr(engine, 'npc_states') and engine.npc_states is not None:
            from .schemas import NPCState, Stats, RelationEntry  # 在 if 块顶部导入一次
            from .mbti_styles import assign_mbti_to_npc, mbti_to_decision_style  # [Bug] 修复decision_style硬编码
            future_chars = self.timeline.get_future_characters(
                timeline_id, exclude_chars=[character_name]
            )
            for fc in future_chars:
                if not fc.get("is_alive", True):
                    continue  # 已死亡角色不注入
                npc_agent_id = f"novel_future_{fc['name']}"
                if npc_agent_id in engine.npc_states:
                    continue  # 避免重复
                # 构建 dormant NPC
                goals = fc.get("goals", [])
                long_term_goal = goals[0] if goals else ""
                short_term_goals = goals[1:4] if len(goals) > 1 else []
                future_npc = NPCState(
                    agent_id=npc_agent_id,
                    name=fc["name"],
                    age=20,
                    role=fc.get("status", ""),
                    role_type="npc",
                    personality=fc.get("description", "")[:200],
                    current_location=fc.get("location", ""),
                    stats=Stats(),
                    relation_to_player=RelationEntry(),
                    is_dormant=True,  # 休眠状态，等待登场
                    dormant_since_day=1,  # [v1.2] 记录休眠开始日，唤醒时用于推演休眠时长
                    original_chapter=fc.get("first_chapter", -1),
                    appearance_conditions={
                        # 概率登场：每天有 probability_per_day 的概率主动登场
                        # min_day_offset 确保不会立即登场（给玩家探索时间）
                        "min_chapter": fc.get("first_chapter", -1),
                        "locations": [fc.get("location", "")] if fc.get("location") else [],
                        "trigger_events": [],
                        "probability_per_day": 0.08,  # 每天 8% 概率主动登场
                        "min_day_offset": 3,  # 至少 3 天后才可能登场
                    },
                    knowledge_scope={
                        # 目标感知模式：知道自己的目标和动机
                        "knows_goals": goals,
                        "knows_facts": [],  # 不知道具体未来事件
                        "forbidden_knowledge": [],  # 由偏离度系统动态填充
                    },
                    # [Bug] 原先 decision_style 硬编码 "normal"，现在根据性格分配 MBTI 并映射
                    mbti_type=(_mbti_code := assign_mbti_to_npc(fc.get("description", "")[:200], [])),
                    ai_behavior={
                        "personality_traits": [],
                        "current_goal": goals[0] if goals else "",
                        "long_term_goal": long_term_goal,
                        "short_term_goals": short_term_goals,
                        "decision_style": mbti_to_decision_style(_mbti_code),
                    },
                    original_future=[],  # 不在 NPC 上存储未来事件，由伏笔系统统一管理
                )
                engine.npc_states[npc_agent_id] = future_npc
                future_chars_injected += 1
            logger.info("注入 %d 个未来角色为 dormant NPC", future_chars_injected)

        # 3.6 [NovelRoleplay] 注入未来关键事件为伏笔
        if hasattr(engine, 'foreshadow_lifecycle') and engine.foreshadow_lifecycle:
            future_events = self.timeline.get_future_events(
                timeline_id, min_importance=5.0
            )
            for fe in future_events:
                try:
                    engine.foreshadow_lifecycle.insert(
                        content=f"[原著伏笔] {fe['chapter_title']}: {fe['event'][:200]}",
                        day=1,
                        turn=0,
                        importance="high" if fe["importance"] >= 7.0 else "normal",
                        tags=["novel_future", f"chapter_{fe['chapter']}",
                              f"timeline_{fe['time_id']}"],
                        memory=getattr(engine, 'memory', None),
                    )
                    future_events_injected += 1
                except Exception as e:
                    logger.warning("注入未来伏笔失败 (%s): %s", fe.get("time_id"), e)
            logger.info("注入 %d 条未来事件伏笔", future_events_injected)

        # 3.7 [NovelRoleplay] 启用蝴蝶效应偏离度追踪（阶段3）
        if hasattr(engine, 'butterfly') and engine.butterfly:
            engine.butterfly.enable_novel_mode(threshold=30.0)
            logger.info("[NovelRoleplay] 蝴蝶效应偏离度追踪已启用")

        # 4. 构建世界数据
        world_data = self._build_world_from_snapshot(
            snapshot, char_state, character_name
        )

        # 5. 创建游戏世界
        # npc_data_list 参数期望列表，world_data["npcs"] 是字典，需转换
        _npcs_dict = world_data.get("npcs", {})
        _npc_list = list(_npcs_dict.values()) if isinstance(_npcs_dict, dict) else _npcs_dict
        world_id = await asyncio.to_thread(
            engine.create_new_game,
            world_data,
            world_data.get("player_start", {}),
            _npc_list,
            world_data.get("world_name", f"小说世界_{character_name}")
        )

        # [v1.3] 标记 engine 为小说模式（供 world_manager / private_facts 等子系统识别）
        try:
            engine.is_novel_roleplay = True
        except Exception:
            pass

        # [v1.3] 小说模式强制使用第三人称（原著风格）
        # 同步设置到 narrative.style_manager，避免读取全局视角设置
        try:
            _ne = getattr(engine, 'narrative', None)
            _sm = getattr(_ne, 'style_manager', None) if _ne else None
            if _sm is not None:
                _sm.is_novel_roleplay = True
                # 清空缓存让下次重新生成
                if hasattr(_sm, 'invalidate_cache'):
                    _sm.invalidate_cache()
        except Exception:
            pass

        # 6. 设置分歧标记（narrative_engine 可能不存在，需用 getattr 保护）
        _narrative_engine = getattr(engine, 'narrative_engine', None)
        if _narrative_engine and hasattr(_narrative_engine, 'set_divergence_point'):
            _narrative_engine.set_divergence_point(
                timeline_id=timeline_id,
                original_future=snapshot.original_future_events,
                mode="free_divergence"
            )

        # 7. 创建状态快照（作为分支起点，state_history 可能不存在，需用 getattr 保护）
        _state_history = getattr(engine, 'state_history', None)
        if _state_history and hasattr(_state_history, 'save_snapshot'):
            try:
                _state_history.save_snapshot(
                    world_id=world_id,
                    turn=0, day=1, time="清晨",
                    player_state=engine.player_state,
                    world_state=engine.world_state,
                    npc_states=engine.npc_states,
                    narrative_text=snapshot.event_description,
                    player_input="",
                    diff_summary=f"小说人物扮演: {character_name} @ {timeline_id}",
                    branch_id="novel_roleplay",
                    parent_snapshot_id=None,
                    divergence_point=True,
                )
            except Exception as e:
                logger.warning("创建状态快照失败（不影响进入游戏）: %s", e)

        # 8. [NovelRoleplay] 生成剧情介绍（前情提要 + 当前处境）
        intro_text = ""
        try:
            intro_text = await self._generate_intro_narrative(
                snapshot, char_state, character_name
            )
            if intro_text:
                # 写入 narrative_history 作为初始条目，玩家可见
                _day = getattr(engine.world_state, 'current_day', 1) if engine.world_state else 1
                _time = getattr(engine.world_state, 'current_time', '清晨') if engine.world_state else '清晨'
                engine.narrative_history.append({
                    "type": "intro",
                    "day": _day,
                    "time": _time,
                    "text": intro_text,
                    "event_type": "novel_intro",
                })
                logger.info("[NovelRoleplay] 剧情介绍已生成: %d 字", len(intro_text))
        except Exception as e:
            logger.warning("[NovelRoleplay] 生成剧情介绍失败（不影响进入游戏）: %s", e)
            intro_text = ""

        logger.info("进入小说人物扮演: 角色=%s, 时间=%s, 世界=%s",
                    character_name, timeline_id, world_id)

        return {
            "success": True,
            "world_id": world_id,
            "character": character_name,
            "timeline_id": timeline_id,
            "memories_injected": len(memories),
            "original_future_events": len(snapshot.original_future_events),
            "future_chars_injected": future_chars_injected,
            "future_events_injected": future_events_injected,
            "intro": intro_text,
        }

    def _build_world_from_snapshot(self, snapshot, char_state,
                                     character_name: str) -> dict:
        """
        从时间轴快照构建游戏世界数据。
        复用 GameEngine 期望的世界数据格式。
        """
        # 从快照构建NPC字典（create_new_game 期望 dict 格式以便 .items() 遍历）
        npcs = {}
        _cs_count = len(snapshot.character_snapshots)
        logger.info("_build_world_from_snapshot: character_snapshots=%d, character_name=%s",
                    _cs_count, character_name)
        for name, snap in snapshot.character_snapshots.items():
            if name == character_name:
                continue  # 玩家角色不作为NPC
            # [Bug] 原代码用 `if not isinstance(snap, type):` 判断，但 CharacterSnapshot
            #       实例不是 type，导致这个分支总是进入，而 dict 分支又不匹配，
            #       最终所有 CharacterSnapshot 对象都被跳过，NPC 数量为 0。
            #       修复：直接判断是否是 dict 或对象
            if isinstance(snap, dict):
                # dict 格式（序列化后）
                npc_name = snap.get("name", name)
                if not snap.get("is_alive", True):
                    continue
                npcs[npc_name] = {
                    "agent_id": f"npc_{uuid.uuid4().hex[:8]}",
                    "name": npc_name,
                    "description": snap.get("description", ""),
                    "status": snap.get("status", ""),
                    "location": snap.get("location", ""),
                    "current_location": snap.get("location", ""),
                    "is_alive": snap.get("is_alive", True),
                    "tags": snap.get("tags", []),
                    "personality": snap.get("personality", ""),
                    "speaking_style": snap.get("speaking_style", ""),
                    "age": snap.get("age", 20),
                }
            else:
                # CharacterSnapshot 对象
                if not getattr(snap, 'is_alive', True):
                    continue
                _npc_name = getattr(snap, 'name', name)
                _loc = getattr(snap, 'location', "")
                npcs[_npc_name] = {
                    "agent_id": f"npc_{uuid.uuid4().hex[:8]}",
                    "name": _npc_name,
                    "description": getattr(snap, 'description', ""),
                    "status": getattr(snap, 'status', ""),
                    "location": _loc,
                    "current_location": _loc,
                    "is_alive": getattr(snap, 'is_alive', True),
                    "tags": getattr(snap, 'tags', []) or [],
                    "personality": getattr(snap, 'personality', ""),
                    "speaking_style": getattr(snap, 'speaking_style', ""),
                    "age": getattr(snap, 'age', 20),
                }

        logger.info("_build_world_from_snapshot: 构建了 %d 个NPC (从 %d 个角色快照中)",
                    len(npcs), _cs_count)

        # 构建地点列表（WorldState.locations 要求 dict[str, dict] 格式）
        locations = {}
        world_state = snapshot.world_state or {}
        _raw_locs = world_state.get("locations", [])
        if isinstance(_raw_locs, dict):
            # 已经是字典格式，直接使用
            for loc_name, loc_info in _raw_locs.items():
                locations[str(loc_name)] = loc_info if isinstance(loc_info, dict) else {"description": str(loc_info)}
        else:
            # 列表格式，转换为字典
            for loc in _raw_locs:
                if isinstance(loc, dict) and "name" in loc:
                    locations[str(loc["name"])] = {"description": loc.get("description", "")}
                elif isinstance(loc, str):
                    locations[loc] = {"description": ""}

        # 玩家起始数据
        player_start = {
            "name": character_name,
            "age": getattr(char_state, 'age', 0) or (
                char_state.get("age", 0) if isinstance(char_state, dict) else 0
            ),
            "description": (
                getattr(char_state, 'description', "")
                if not isinstance(char_state, dict)
                else char_state.get("description", "")
            ),
            "starting_location": (
                getattr(char_state, 'location', "")
                if not isinstance(char_state, dict)
                else char_state.get("location", "")
            ) or "未知地点",
        }

        # 世界数据
        return {
            "world_name": self._import_status.get("novel_name", "小说世界"),
            "world_type": "novel",
            "description": snapshot.event_description or "基于小说的世界",
            "era_name": "小说世界",
            "era_year": "",
            "initial_event": (
                f"你成为了{character_name}，时间停留在：{snapshot.chapter_title or snapshot.event_description[:50]}"
            ),
            "npcs": dict(list(npcs.items())[:20]),  # 限制NPC数量（字典格式）
            "locations": locations,
            "player_start": player_start,
            "power_system": "",
            "social_structure": "",
            "core_conflict": "",
            "is_novel_roleplay": True,
            "novel_timeline_id": snapshot.time_id,
        }

    async def _generate_intro_narrative(self, snapshot, char_state,
                                          character_name: str) -> str:
        """
        [NovelRoleplay] 调用 LLM 生成进入游戏时的剧情介绍。

        内容包含两部分：
        1. 前情提要：基于累积摘要简述到此时间点为止的关键情节
        2. 当前处境：描述主角此刻面临的场景、问题、机遇

        失败时返回空字符串（不影响进入游戏）。
        """
        if not self.llm:
            logger.warning("[NovelRoleplay] LLM 未配置，跳过剧情介绍生成")
            return ""

        # 提取快照字段（兼容 dict 和对象）
        def _get(obj, key, default=""):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        # 前情摘要（累积摘要列表）
        summaries = getattr(snapshot, 'accumulated_summaries', []) or []
        if isinstance(summaries, list):
            summaries_text = "\n".join(
                f"{i+1}. {s}" for i, s in enumerate(summaries) if s
            )
        else:
            summaries_text = str(summaries)
        if not summaries_text.strip():
            summaries_text = "（暂无前情摘要）"

        # 当前时间点信息
        chapter_title = getattr(snapshot, 'chapter_title', '') or ''
        story_time = getattr(snapshot, 'story_time', '') or ''
        event_desc = getattr(snapshot, 'event_description', '') or ''
        novel_name = self._import_status.get("novel_name", "小说世界")

        # 主角状态
        age = _get(char_state, 'age', '未知')
        status = _get(char_state, 'status', '')
        desc = _get(char_state, 'description', '')
        location = _get(char_state, 'location', '未知')
        skills = _get(char_state, 'skills', []) or []
        inventory = _get(char_state, 'inventory', []) or []
        goals = _get(char_state, 'goals', []) or []
        emotion = _get(char_state, 'emotional_state', '')
        relationships = _get(char_state, 'relationships', {}) or {}

        skills_text = "、".join(skills) if skills else "无"
        inv_text = "、".join(inventory) if inventory else "身无长物"
        goals_text = "；".join(goals) if goals else "尚未明确"
        rel_text = ""
        if isinstance(relationships, dict):
            rel_text = "\n".join(
                f"  - {k}: {v}" for k, v in relationships.items() if v
            )
        if not rel_text:
            rel_text = "  - 暂无显著关系"

        prompt = f"""你是一位资深的小说叙事大师。玩家即将扮演小说《{novel_name}》中的角色「{character_name}」，进入小说世界展开自由推演。

【前情提要（到此时间点为止的关键情节）】
{summaries_text}

【当前时间点】
章节：{chapter_title or '未知'}
故事时间：{story_time or '未明确'}
当下事件：{event_desc or '（无具体事件描述）'}

【主角当前状态】
姓名：{character_name}
年龄：{age}
身份地位：{status or '未明确'}
人物描述：{desc or '（无描述）'}
所在地点：{location}
技能：{skills_text}
随身物品：{inv_text}
当前目标：{goals_text}
情绪状态：{emotion or '未明确'}
人际关系：
{rel_text}

【任务】
请为玩家生成一段进入游戏前的剧情介绍，必须包含两部分：

第一部分【前情提要】：用文学化的笔触简述到此时间点为止小说中发生的关键情节、人物关系、世界格局。让玩家快速进入故事氛围（约 300-800 字）。

第二部分【当前处境】：聚焦于主角{character_name}此刻——他所处的场景、面临的境遇、悬而未决的问题、可把握的机遇。让玩家明确"我现在该做什么"（约 200-500 字）。

【硬性要求】
1. 全文使用第二人称"你"来称呼玩家（"你站在..."、"你回想起..."）
2. 文笔要符合原著的叙事风格与时代感
3. 严禁剧透原著此时间点之后的情节
4. 总长度 500-1500 字
5. 两部分之间用一个空行分隔
6. 不要加任何标题、Markdown 标记、序号或解释性文字
7. 直接输出正文内容

请开始撰写："""

        def _do_call() -> str:
            return self.llm.chat(
                prompt,
                temperature=0.75,
                max_tokens=2048,
            )

        try:
            result = await asyncio.to_thread(_do_call)
            if isinstance(result, str):
                result = result.strip()
            else:
                result = str(result).strip()
            if not result:
                logger.warning("[NovelRoleplay] LLM 返回空内容")
            return result
        except Exception as e:
            logger.error("[NovelRoleplay] LLM 调用失败: %s", e)
            return ""

    # ── 查询 ──────────────────────────────────────────────

    def get_stats(self) -> dict:
        """获取当前状态统计"""
        return {
            "import_status": self._import_status.copy(),
            "kb_stats": self.knowledge_base.get_stats(),
            "timeline_nodes": len(self.timeline.nodes),
            "key_events": len(self.timeline.get_key_events()),
            "characters": len(self.get_characters()),
        }

    def is_ready(self) -> bool:
        """是否已导入小说且可以进入角色选择"""
        return self._import_status["state"] == "done"
