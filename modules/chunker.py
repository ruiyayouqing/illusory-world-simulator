"""
[v12] 小说智能分块器 — 将百万字小说分割为语义完整的块。

三层递进策略：
1. 结构分块：按章节标题切分（正则匹配）
2. 语义边界调整：过大的块二次切分，过小的块合并
3. 重叠窗口：相邻块保留重叠文本，保证上下文连续性

特点：
- 零依赖（纯Python正则+文本处理）
- 支持中文章节标题（第X章、卷X、序章等）
- 自适应块大小（目标2000-5000字）
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("chronoverse.chunker")


@dataclass
class TextChunk:
    """文本块"""
    text: str
    index: int                    # 块序号
    char_start: int = 0           # 原文起始位置
    char_end: int = 0             # 原文结束位置
    chapter_title: str = ""       # 所属章节标题（如有）
    chunk_size: int = 0           # 块字符数


class SemanticChunker:
    """
    [v12] 智能分块器。

    使用方式：
        chunker = SemanticChunker(target_size=3000, overlap=200)
        chunks = chunker.chunk(text)
    """

    # 中文章节标题正则
    CHAPTER_PATTERNS = [
        r'第[一二三四五六七八九十百千零〇0-9]+[章节回卷集部篇]',
        r'第\d+章',
        r'第\d+节',
        r'Chapter\s+\d+',
        r'CHAPTER\s+\d+',
        r'【序章】|【楔子】|【尾声】|【后记】',
        r'^序章|^楔子|^尾声|^后记|^前言|^引子',
        r'卷[一二三四五六七八九十]',
    ]

    def __init__(self, target_size: int = 3000,
                 min_size: int = 500,
                 max_size: int = 6000,
                 overlap: int = 200):
        """
        target_size: 目标块大小（字符）
        min_size: 最小块大小（小于此值合并到相邻块）
        max_size: 最大块大小（大于此值二次切分）
        overlap: 相邻块重叠字符数
        """
        self.target_size = target_size
        self.min_size = min_size
        self.max_size = max_size
        self.overlap = overlap
        # 编译章节正则
        self._chapter_regex = re.compile(
            '|'.join(f'(?:{p})' for p in self.CHAPTER_PATTERNS),
            re.MULTILINE
        )

    def chunk(self, text: str) -> list[TextChunk]:
        """
        将文本分割为语义完整的块。
        """
        if not text or len(text) < self.min_size:
            return [TextChunk(text=text, index=0,
                              char_start=0, char_end=len(text),
                              chunk_size=len(text))]

        # 第一层：按章节切分
        sections = self._split_by_chapters(text)

        # 第二层：对每个 section 进行大小调整
        chunks = []
        for section_text, section_title, start_pos in sections:
            if len(section_text) > self.max_size:
                # 过大：二次切分
                sub_chunks = self._split_large_section(
                    section_text, section_title, start_pos
                )
                chunks.extend(sub_chunks)
            elif len(section_text) < self.min_size and chunks:
                # 过小：合并到前一个块
                chunks[-1].text += "\n" + section_text
                chunks[-1].char_end = start_pos + len(section_text)
                chunks[-1].chunk_size = len(chunks[-1].text)
            else:
                chunks.append(TextChunk(
                    text=section_text,
                    index=len(chunks),
                    char_start=start_pos,
                    char_end=start_pos + len(section_text),
                    chapter_title=section_title,
                    chunk_size=len(section_text),
                ))

        # 第三层：添加重叠窗口
        chunks = self._add_overlap(chunks)

        # 重新编号
        for i, chunk in enumerate(chunks):
            chunk.index = i

        logger.info("分块完成: %d 字 → %d 块 (平均 %d 字/块)",
                    len(text), len(chunks),
                    len(text) // max(1, len(chunks)))
        return chunks

    def _split_by_chapters(self, text: str) -> list[tuple[str, str, int]]:
        """
        按章节标题切分文本。
        返回：[(文本, 章节标题, 起始位置), ...]
        """
        matches = list(self._chapter_regex.finditer(text))

        if not matches:
            # 无章节标题：整体作为一个 section
            return [(text, "", 0)]

        sections = []
        # 章节标题之前的内容（序言等）
        if matches[0].start() > 0:
            pre_text = text[:matches[0].start()].strip()
            if pre_text:
                sections.append((pre_text, "序言", 0))

        for i, match in enumerate(matches):
            title = match.group().strip()
            start = match.start()
            if i + 1 < len(matches):
                end = matches[i + 1].start()
            else:
                end = len(text)
            section_text = text[start:end].strip()
            if section_text:
                sections.append((section_text, title, start))

        return sections

    def _split_large_section(self, text: str, title: str,
                              start_pos: int) -> list[TextChunk]:
        """
        对过大的 section 按段落边界二次切分。
        """
        # 按段落分割
        paragraphs = re.split(r'\n\s*\n', text)
        chunks = []
        current_text = ""
        current_start = 0

        for para in paragraphs:
            if not para.strip():
                continue

            if len(current_text) + len(para) > self.target_size and current_text:
                # 当前块已够大，保存并开始新块
                chunks.append(TextChunk(
                    text=current_text.strip(),
                    index=len(chunks),
                    char_start=start_pos + current_start,
                    char_end=start_pos + current_start + len(current_text),
                    chapter_title=title,
                    chunk_size=len(current_text.strip()),
                ))
                current_start += len(current_text) + 2  # +2 for \n\n
                current_text = para
            else:
                if not current_text:
                    current_text = para
                else:
                    current_text += "\n\n" + para

        # 最后一块
        if current_text.strip():
            chunks.append(TextChunk(
                text=current_text.strip(),
                index=len(chunks),
                char_start=start_pos + current_start,
                char_end=start_pos + current_start + len(current_text),
                chapter_title=title,
                chunk_size=len(current_text.strip()),
            ))

        return chunks

    def _add_overlap(self, chunks: list[TextChunk]) -> list[TextChunk]:
        """
        为相邻块添加重叠窗口。
        """
        if self.overlap <= 0 or len(chunks) <= 1:
            return chunks

        for i in range(1, len(chunks)):
            prev_text = chunks[i - 1].text
            overlap_text = prev_text[-self.overlap:] if len(prev_text) > self.overlap else prev_text
            chunks[i].text = overlap_text + "\n" + chunks[i].text
            chunks[i].chunk_size = len(chunks[i].text)

        return chunks

    def get_chunk_summaries(self, chunks: list[TextChunk]) -> list[dict]:
        """获取块摘要信息（用于进度展示）"""
        return [
            {
                "index": c.index,
                "chapter": c.chapter_title,
                "size": c.chunk_size,
                "preview": c.text[:100] + "..." if len(c.text) > 100 else c.text,
            }
            for c in chunks
        ]
