"""
[v12] 小说知识库 — 封装章节扫描、角色提取、图谱构建。
被 NovelRoleplayService 使用。
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING, Callable, List, Optional

if TYPE_CHECKING:
    from .llm.base_llm import BaseLLM
    from .graph_rag import GraphRAG

from .chapter_scanner import ChapterScanner, ChapterInfo
from .graph_rag import GraphRAG, GraphEntity, GraphRelation

logger = logging.getLogger("chronoverse.novel_kb")

QUICK_CHARACTER_PROMPT = """你是一位资深文学编辑。请从以下小说片段（可能是多章首段拼接）中，识别出主要角色。

要求：
1. 只返回真正的重要角色（主角、重要配角），不要列出路人甲
2. 每个角色给出：姓名、性别、身份/职业、简短性格描述、重要性评分(1-10)
3. 严格返回JSON格式，不要有任何额外文字

返回格式：
{{"characters": [
    {{"name": "角色名", "gender": "男/女/未知", "role": "身份/职业",
      "personality": "简短性格描述", "importance": 9, "description": "20字内人物简介"}}
]}}

小说片段：
{text}
"""

DEEP_EXTRACT_PROMPT = """你是一位文学分析专家。请从以下小说文本中提取关键实体和关系。

要求：
1. 提取主要人物实体（姓名、身份、性格）
2. 提取人物之间的关系（谁和谁是什么关系）
3. 提取重要地点
4. 提取关键事件
5. 严格返回JSON格式

返回格式：
{{
  "entities": [
    {{"name": "实体名", "type": "person/location/event", "description": "描述"}}
  ],
  "relations": [
    {{"source": "实体A", "target": "实体B", "relation_type": "关系类型", "description": "关系描述"}}
  ]
}}

小说文本：
{text}
"""


class NovelKnowledgeBase:
    """小说知识库：管理小说的章节结构、角色、知识图谱"""

    def __init__(self, llm=None, embedding_func=None, storage_dir: str = None):
        self.llm = llm
        self.embedding_func = embedding_func
        self.storage_dir = storage_dir
        if storage_dir:
            os.makedirs(storage_dir, exist_ok=True)

        self._scanner = ChapterScanner()
        self._graph_rag = GraphRAG(llm=llm)
        self._chapters: List[ChapterInfo] = []
        self._characters: List[dict] = []
        self._text = ""

        self._stats = {
            "total_chars": 0,
            "chunks": 0,
            "chapters": 0,
            "entities": 0,
            "relations": 0,
        }

    def _progress(self, cb: Optional[Callable], msg: str):
        if cb:
            try:
                cb(msg)
            except Exception:
                pass
        logger.info("[KB] %s", msg)

    async def quick_ingest(self, text: str,
                           progress_callback: Callable = None,
                           pre_chunked: list = None) -> dict:
        """
        [v12] 快速导入：只扫描章节 + 提取主要角色。不做深度图谱。
        """
        self._text = text
        self._stats["total_chars"] = len(text)

        self._progress(progress_callback, "正在扫描章节结构...")
        self._chapters = self._scanner.scan_chapters(text)
        self._stats["chapters"] = len(self._chapters)
        self._progress(progress_callback, f"检测到 {len(self._chapters)} 个章节")

        self._progress(progress_callback, "正在分析主要角色...")
        self._characters = await self._extract_characters_quick(text, progress_callback)
        self._stats["entities"] = len(self._characters)

        for ch in self._characters:
            name = ch["name"]
            if name and name not in self._graph_rag.entities:
                entity = GraphEntity(
                    name=name,
                    entity_type="person",
                    description=ch.get("description", ch.get("role", "")),
                )
                entity.mention_count = ch.get("importance", 5)
                entity.source_type = "novel"
                self._graph_rag.entities[name] = entity

        self._stats["chunks"] = len(pre_chunked) if pre_chunked else 0
        self._progress(progress_callback, f"识别到 {len(self._characters)} 个主要角色")

        return {
            "entities": len(self._characters),
            "chapters": len(self._chapters),
            "characters": self._characters,
        }

    async def _extract_characters_quick(self, text: str, cb=None) -> list:
        """快速提取角色：拼接各章首段，一次LLM调用"""
        if not self.llm:
            logger.warning("LLM不可用，使用启发式角色提取")
            return self._extract_characters_heuristic(text)

        try:
            segments = []
            for ch in self._chapters[:80]:
                if ch.first_segment:
                    segments.append(ch.first_segment)
            sample_text = "\n\n".join(segments)
            if len(sample_text) > 8000:
                sample_text = sample_text[:8000]

            prompt = QUICK_CHARACTER_PROMPT.format(text=sample_text)
            result = await self._call_llm_json(prompt)
            chars = result.get("characters", [])
            if not chars:
                chars = self._extract_characters_heuristic(text)
            return chars
        except Exception as e:
            logger.warning("LLM角色提取失败，使用启发式: %s", e)
            return self._extract_characters_heuristic(text)

    def _extract_characters_heuristic(self, text: str) -> list:
        """无LLM时的启发式角色提取：基于对话标签统计"""
        try:
            candidates = self._scanner.scan_character_candidates(text, self._chapters)
            chars = []
            for c in candidates[:12]:
                chars.append({
                    "name": c.name,
                    "gender": "未知",
                    "role": "小说角色",
                    "personality": "",
                    "importance": min(10, c.mention_count),
                    "description": f"出现{c.mention_count}次的角色",
                })
            return chars
        except Exception as e:
            logger.warning("启发式角色提取失败: %s", e)
            return []

    async def _call_llm_json(self, prompt: str) -> dict:
        """异步调用LLM返回JSON"""
        import asyncio
        def _do_call():
            try:
                return self.llm.chat_json(prompt, temperature=0.3, max_tokens=0)
            except Exception as e:
                logger.error("LLM调用失败: %s", e)
                raise
        return await asyncio.to_thread(_do_call)

    def get_chapters(self) -> list:
        """返回章节列表，每个章节包含 index, title, char_start, char_end 等"""
        result = []
        for ch in self._chapters:
            result.append({
                "index": ch.index,
                "title": ch.title,
                "char_start": ch.char_start,
                "char_end": ch.char_end,
                "length": ch.length,
                "first_segment": ch.first_segment,
                "is_auto_split": ch.is_auto_split,
            })
        return result

    def get_main_characters(self, top_n: int = 12) -> list:
        """返回主要角色列表"""
        sorted_chars = sorted(
            self._characters,
            key=lambda x: x.get("importance", 0),
            reverse=True,
        )
        return sorted_chars[:top_n]

    async def deep_ingest_partial(self, text: str,
                                   progress_callback: Callable = None,
                                   existing_chunks: list = None,
                                   character_focus: str = "") -> dict:
        """
        [v12] 深度处理部分文本：提取实体+关系，构建图谱。
        """
        self._progress(progress_callback, "正在深度分析实体关系...")

        entities_count = 0
        relations_count = 0

        if not self.llm:
            self._progress(progress_callback, "LLM不可用，跳过深度图谱构建")
            for ch in self._characters:
                name = ch["name"]
                if name and name not in self._graph_rag.entities:
                    entity = GraphEntity(
                        name=name,
                        entity_type="person",
                        description=ch.get("description", ""),
                    )
                    entity.source_type = "novel"
                    self._graph_rag.entities[name] = entity
                    entities_count += 1
            return {"entities": entities_count, "relations": 0}

        try:
            chunks_to_process = []
            chunk_size = 4000
            for i in range(0, len(text), chunk_size):
                chunk = text[i:i + chunk_size]
                chunks_to_process.append(chunk)

            total_chunks = len(chunks_to_process)
            for idx, chunk in enumerate(chunks_to_process[:20]):
                self._progress(progress_callback,
                    f"深度分析中... ({idx+1}/{min(total_chunks, 20)})")
                try:
                    prompt = DEEP_EXTRACT_PROMPT.format(text=chunk)
                    result = await self._call_llm_json(prompt)
                    for ent in result.get("entities", []):
                        name = ent.get("name", "")
                        if not name:
                            continue
                        if name not in self._graph_rag.entities:
                            entity = GraphEntity(
                                name=name,
                                entity_type=ent.get("type", "unknown"),
                                description=ent.get("description", ""),
                            )
                            entity.source_type = "novel"
                            self._graph_rag.entities[name] = entity
                            entities_count += 1
                        else:
                            self._graph_rag.entities[name].mention_count += 1
                    for rel in result.get("relations", []):
                        src = rel.get("source", "")
                        tgt = rel.get("target", "")
                        if src and tgt:
                            relation = GraphRelation(
                                source=src,
                                target=tgt,
                                relation_type=rel.get("relation_type", "related_to"),
                                description=rel.get("description", ""),
                            )
                            self._graph_rag.relations.append(relation)
                            relations_count += 1
                except Exception as e:
                    logger.warning("深度处理块 %d 失败: %s", idx, e)
                    continue

        except Exception as e:
            logger.error("深度处理异常: %s", e, exc_info=True)
            raise

        self._stats["entities"] = entities_count
        self._stats["relations"] = relations_count
        self._progress(progress_callback,
            f"深度分析完成: {entities_count} 实体, {relations_count} 关系")

        return {"entities": entities_count, "relations": relations_count}

    def get_graph_rag(self) -> "GraphRAG":
        """返回知识图谱对象"""
        return self._graph_rag

    def get_stats(self) -> dict:
        """返回统计信息"""
        return {
            **self._stats,
            "graph_entities": len(self._graph_rag.entities) if self._graph_rag else 0,
            "graph_relations": len(self._graph_rag.relations) if self._graph_rag else 0,
        }
