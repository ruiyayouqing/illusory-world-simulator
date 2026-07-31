"""
[v12] 小说章节扫描器 — 利用网文小说结构化的章节标题做秒级预分析。

零依赖、零 LLM 调用。可独立于分块器使用，提供：
1. 章节标题扫描：正则识别所有"第X章 XXX"，返回章节列表
2. 章节首段提取：每章首 N 字，用于批量LLM提取
3. 疑似人名扫描：识别"XXX道:""XXX说:"等对话标签

这些预处理结果能让 NovelKnowledgeBase 跳过盲提取，
直接知道小说结构、候选人物，再交由 LLM 一次批量确认。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger("chronoverse.chapter_scanner")

# [v12+] 连续空行正则：用于无章节标题时的回退分章
# 匹配 \n + 任意空白行 + \n（至少两个换行，中间可有空格/制表符）
_BLANK_LINE_RE = re.compile(r'\n[ \t]*\n+')


@dataclass
class ChapterInfo:
    """章节信息"""
    index: int                       # 章节序号（0-based）
    title: str                       # 章节标题（如"第一章 风起"）
    char_start: int = 0              # 章节在原文起始位置
    char_end: int = 0                # 章节在原文结束位置
    first_segment: str = ""          # 章节首段（默认前300字）
    length: int = 0                  # 章节字符数
    is_auto_split: bool = False      # [v12+] 是否为自动分章（无章节标题时的回退分章）


@dataclass
class CharacterCandidate:
    """疑似人物候选"""
    name: str                        # 候选名（2-4字中文）
    mention_count: int = 0           # 出现次数（"XXX道:"等模式匹配）
    first_chapter: int = -1          # 首次出现章节序号
    sample_contexts: List[str] = field(default_factory=list)  # 代表性上下文片段


class ChapterScanner:
    """
    [v12] 章节结构扫描器。

    使用方式：
        scanner = ChapterScanner()
        chapters = scanner.scan_chapters(text)
        candidates = scanner.scan_character_candidates(text, chapters)
    """

    # 章节标题正则（与 chunker 对齐，但增加标题正文捕获）
    CHAPTER_PATTERNS = [
        r'第[一二三四五六七八九十百千零〇0-9]+[章节回卷集部篇][^\n]{0,40}',
        r'Chapter\s+\d+[^\n]{0,40}',
        r'CHAPTER\s+\d+[^\n]{0,40}',
        r'【序章】[^\n]{0,20}|【楔子】[^\n]{0,20}|【尾声】[^\n]{0,20}|【后记】[^\n]{0,20}',
        r'^序章[^\n]{0,20}|^楔子[^\n]{0,20}|^尾声[^\n]{0,20}|^后记[^\n]{0,20}',
    ]

    # 对话标签正则：识别"XXX道:""XXX说:""XXX笑道:"等
    # 关键修复1：动词组从长到短排序，避免"有人说道"被拆为"有人说"+"道"
    # 关键修复2：动词部分作为独立组，不混入名字组
    DIALOGUE_PATTERN = r'([\u4e00-\u9fa5]{2,4})(?:微微|哈哈|冷冷|淡淡|忽然|突然|低声|高声|大声|轻声|缓缓|猛地|猛然|怔了怔|愣了愣)?(冷笑道|大笑道|低声道|高声喊道|笑道|说道|喊道|问道|答道|怒道|叹道|惊道|道|说)'

    def __init__(self, first_segment_size: int = 300,
                 fallback_target_chars: int = 5000):
        """
        first_segment_size: 每章首段保留字符数。
        默认300字，320章×300字≈10万字，约30万tokens，可一次塞入1M上下文。

        fallback_target_chars: 无章节标题时的回退分章目标字数（默认5000字/章）。
        """
        self.first_segment_size = first_segment_size
        self.fallback_target_chars = fallback_target_chars
        self._chapter_regex = re.compile(
            '|'.join(f'(?:{p})' for p in self.CHAPTER_PATTERNS),
            re.MULTILINE
        )
        self._dialogue_regex = re.compile(self.DIALOGUE_PATTERN)

    def scan_chapters(self, text: str) -> List[ChapterInfo]:
        """
        扫描全文，返回所有章节信息。

        如果小说没有标准章节标题，会自动回退到 fallback_chunk_split
        按段落+字数阈值分章，确保下游流程总能拿到章节列表。
        """
        if not text:
            return []

        matches = list(self._chapter_regex.finditer(text))

        if not matches:
            # 无章节标题：回退到段落+字数阈值分章
            logger.info("未检测到章节标题，回退到自动分章（按段落+%d字阈值）",
                        self.fallback_target_chars)
            return self.fallback_chunk_split(text)

        chapters = []
        for i, m in enumerate(matches):
            title = m.group(0).strip()
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            # 章节首段：跳过标题行，取后续内容（不包含下一章标题）
            title_end = m.end()
            # 找到标题后的第一个换行
            newline_pos = text.find('\n', title_end)
            if newline_pos != -1 and newline_pos < title_end + 100:
                body_start = newline_pos + 1
            else:
                body_start = title_end
            # 章节首段长度上限 = 配置大小，但不超过章节剩余长度
            seg_end = min(body_start + self.first_segment_size, end)
            first_seg = text[body_start:seg_end].strip()
            chapters.append(ChapterInfo(
                index=i,
                title=title,
                char_start=start,
                char_end=end,
                first_segment=first_seg,
                length=end - start,
            ))

        return chapters

    def fallback_chunk_split(self, text: str,
                              target_chars: int = None) -> List[ChapterInfo]:
        """
        [v12+] 无章节标题时的回退分章：按段落（连续空行）累积切分。

        策略：
        1. 按"连续空行"切段（场景边界）
        2. 段落累积达到 target_chars 切一章
        3. 若全文无空行（极端情况），按 target_chars 硬切

        参数：
            text: 全文
            target_chars: 每章目标字数，默认 5000
        """
        if not text:
            return []

        if target_chars is None:
            target_chars = self.fallback_target_chars

        # 找到所有非空段落 [start, end]，以"连续空行"为分隔
        paragraphs: list[tuple[int, int]] = []
        prev_end = 0
        for m in _BLANK_LINE_RE.finditer(text):
            seg_start = prev_end
            seg_end = m.start()
            if text[seg_start:seg_end].strip():
                paragraphs.append((seg_start, seg_end))
            prev_end = m.end()
        # 最后一段
        if prev_end < len(text) and text[prev_end:].strip():
            paragraphs.append((prev_end, len(text)))

        # 极端情况：全文无空行 → 按 target_chars 硬切
        if not paragraphs:
            chapters = []
            total = len(text)
            for i in range(0, total, target_chars):
                end = min(i + target_chars, total)
                ch_text = text[i:end]
                chapters.append(ChapterInfo(
                    index=len(chapters),
                    title=f"第{len(chapters) + 1}段（自动分章）",
                    char_start=i,
                    char_end=end,
                    first_segment=ch_text[:self.first_segment_size].strip(),
                    length=end - i,
                    is_auto_split=True,
                ))
            return chapters

        # 边界修复：如果只有1个段落但超过 target_chars*2，按 target_chars 硬切
        if len(paragraphs) == 1 and (paragraphs[0][1] - paragraphs[0][0]) >= target_chars * 2:
            chapters = []
            p_start, p_end = paragraphs[0]
            for i in range(p_start, p_end, target_chars):
                end = min(i + target_chars, p_end)
                ch_text = text[i:end]
                chapters.append(ChapterInfo(
                    index=len(chapters),
                    title=f"第{len(chapters) + 1}段（自动分章）",
                    char_start=i,
                    char_end=end,
                    first_segment=ch_text[:self.first_segment_size].strip(),
                    length=end - i,
                    is_auto_split=True,
                ))
            return chapters

        # 段落累积切章
        chapters: list[ChapterInfo] = []
        ch_idx = 0
        ch_start = paragraphs[0][0]
        ch_end = paragraphs[0][1]
        ch_len = ch_end - ch_start

        for i in range(1, len(paragraphs)):
            p_start, p_end = paragraphs[i]

            if ch_len >= target_chars:
                # 切章
                chapters.append(self._make_auto_chapter(
                    ch_idx, text, ch_start, ch_end
                ))
                ch_idx += 1
                ch_start = p_start
                ch_end = p_end
                ch_len = p_end - p_start
            else:
                # 累积到当前章
                ch_end = p_end
                ch_len = ch_end - ch_start

        # 最后一段
        if ch_end > ch_start:
            chapters.append(self._make_auto_chapter(
                ch_idx, text, ch_start, ch_end
            ))

        logger.info("自动分章完成: %d 段 → %d 章（目标每章%d字）",
                    len(paragraphs), len(chapters), target_chars)
        return chapters

    def _make_auto_chapter(self, idx: int, text: str,
                            start: int, end: int) -> ChapterInfo:
        """构造一个自动分章的 ChapterInfo"""
        ch_text = text[start:end]
        return ChapterInfo(
            index=idx,
            title=f"第{idx + 1}段（自动分章）",
            char_start=start,
            char_end=end,
            first_segment=ch_text[:self.first_segment_size].strip(),
            length=end - start,
            is_auto_split=True,
        )

    def scan_character_candidates(
        self,
        text: str,
        chapters: List[ChapterInfo] = None,
        min_mentions: int = 3,
        max_candidates: int = 100,
    ) -> List[CharacterCandidate]:
        """
        扫描全文，识别疑似人物名候选。

        基于"XXX道:""XXX说:"等对话标签。
        返回出现次数 >= min_mentions 的候选名单。

        参数：
            text: 全文
            chapters: 可选，章节列表，用于标记首次出现章节
            min_mentions: 最小出现次数阈值（过滤偶发匹配）
            max_candidates: 最大候选数（按出现次数排序）
        """
        if not text:
            return []

        # 构建章节位置索引（用于快速查找某位置所属章节）
        chapter_starts = [c.char_start for c in chapters] if chapters else [0]

        def find_chapter(pos: int) -> int:
            if not chapters:
                return 0
            # 二分查找
            import bisect
            idx = bisect.bisect_right(chapter_starts, pos) - 1
            return max(0, idx)

        # 统计每个名字出现次数
        name_counter: dict[str, int] = {}
        name_contexts: dict[str, list[str]] = {}
        name_first_chapter: dict[str, int] = {}

        for m in self._dialogue_regex.finditer(text):
            name = m.group(1)
            # 循环剥离：交替剥离动词/副词字和代词/称呼字，直到稳定
            # 这样"是便有人"→剥"人"→"是便有"→剥"有"→"是便"→停止
            prev_name = None
            while prev_name != name and len(name) >= 3:
                prev_name = name
                # 剥离末尾的动词/副词字（如"林渊问"→"林渊"）
                while len(name) >= 3 and name[-1] in TRAILING_VERB_CHARS:
                    name = name[:-1]
                # 剥离末尾的"一"（如"微微一笑"→"微微一"→"微微"）
                while len(name) >= 3 and name.endswith("一"):
                    name = name[:-1]
                # 剥离末尾的代词/通用称呼字（如"有人"→"有"）
                while len(name) >= 3 and name[-1] in TRAILING_PRONOUN_CHARS:
                    name = name[:-1]
                # 剥离末尾的武器/物品字（如"着眉尖刀"→"着眉尖"）
                while len(name) >= 3 and name[-1] in TRAILING_OBJECT_CHARS:
                    name = name[:-1]
                if len(name) < 2:
                    break
            if len(name) < 2:
                continue
            # 检查 NON_CHARACTER_WORDS（在剥离之后检查）
            if name in NON_CHARACTER_WORDS:
                continue
            # 检查名字中是否包含"非人物"子串
            if any(non_human in name for non_human in NON_HUMAN_SUBSTRINGS):
                continue
            # 检查名字末尾或开头是否是单字动词/副词（残留过滤）
            # 如"是便""着眉尖"这种剥离不彻底的残留
            if len(name) <= 3 and name[0] in RESIDUAL_PREFIX_CHARS:
                continue
            if len(name) == 2 and name[-1] in RESIDUAL_SUFFIX_CHARS:
                continue
            pos = m.start()
            name_counter[name] = name_counter.get(name, 0) + 1

            # 记录首次出现章节
            if name not in name_first_chapter:
                ch_idx = find_chapter(pos)
                name_first_chapter[name] = ch_idx
                # 保存代表性上下文（前后30字）
                ctx_start = max(0, pos - 30)
                ctx_end = min(len(text), pos + 40)
                context = text[ctx_start:ctx_end].replace('\n', ' ').strip()
                name_contexts.setdefault(name, []).append(context)
            elif len(name_contexts.get(name, [])) < 2:
                # 补充1个上下文样本
                ctx_start = max(0, pos - 30)
                ctx_end = min(len(text), pos + 40)
                context = text[ctx_start:ctx_end].replace('\n', ' ').strip()
                if context not in name_contexts.get(name, []):
                    name_contexts.setdefault(name, []).append(context)

        # 过滤+排序
        candidates = [
            CharacterCandidate(
                name=name,
                mention_count=count,
                first_chapter=name_first_chapter.get(name, -1),
                sample_contexts=name_contexts.get(name, []),
            )
            for name, count in name_counter.items()
            if count >= min_mentions
        ]
        candidates.sort(key=lambda x: x.mention_count, reverse=True)
        return candidates[:max_candidates]

    def build_chapter_digest(self, chapters: List[ChapterInfo],
                              max_chars: int = 100000) -> str:
        """
        将所有章节首段合并为一个大文本，供 LLM 一次性提取。

        max_chars: 合并后最大字符数（约 30万 tokens）
        """
        parts = []
        total = 0
        for ch in chapters:
            seg = ch.first_segment
            if not seg:
                continue
            entry = f"【{ch.title}】\n{seg}\n\n"
            if total + len(entry) > max_chars:
                # 截断到 max_chars
                remaining = max_chars - total
                if remaining > 100:
                    parts.append(entry[:remaining])
                break
            parts.append(entry)
            total += len(entry)
        return "".join(parts)


# 常见非人名词（对话标签可能误匹配）
NON_CHARACTER_WORDS = {
    # 代词
    "他们", "她们", "我们", "你们", "自己", "大家", "众人", "对方",
    "此人", "此人", "那人", "这人", "别人", "旁人", "他人",
    "有人", "无人", "某人", "一人", "众人", "众人", "二人",
    "老头", "老者", "少年", "少女", "女子", "男子", "妇人", "男子汉",
    "汉子", "书生", "道士", "和尚", "尼姑", "乞丐", "老道", "老僧",
    "老翁", "老妪", "村夫", "农夫", "商人", "客人", "主人", "仆人",
    "侍女", "丫鬟", "婢女", "家丁", "管家", "护院", "镖师", "将军",
    "士兵", "军士", "差役", "衙役", "捕快", "县令", "知府", "皇帝",
    "皇后", "太子", "公主", "王爷", "王妃", "世子", "贵妇", "贵女",
    # 时间
    "此时", "那时", "这时", "同时", "随即", "忽然", "突然", "猛然",
    "良久", "许久", "片刻", "霎时", "顿时", "顷刻",
    # 程度副词
    "微微", "哈哈", "冷冷", "淡淡", "缓缓", "渐渐",
    "微微一", "哈哈大", "冷冷一",
    # 角色通用称呼
    "师兄", "师弟", "师姐", "师妹", "师父", "师傅", "师尊", "道人",
    "仙人", "前辈", "后辈", "小子", "姑娘", "公子", "大人", "陛下",
    "掌门", "长老", "宗主", "门主", "教主", "阁下", "在下",
    # 常用动词/形容词误匹配
    "说道", "笑道", "喊道", "问道", "答道", "怒道", "叹道",
    # "不知道"误匹配
    "不知", "我不知", "他不", "她不", "我不", "你不",
    # 否定/疑问词误匹配
    "不是", "不能", "不要", "不可", "不该", "不会", "没有",
    "什么", "为何", "怎么", "如何", "何以", "岂不", "莫非",
    "难道", "哪怕", "哪怕不",
    # 量词误匹配
    "一个", "两个", "几个", "一些", "一番", "一切",
    # 关联词误匹配
    "于是", "因为", "所以", "但是", "可是", "不过", "而且", "并且",
    "虽然", "尽管", "即使", "哪怕", "如果", "要是", "假如",
    # 助词/连词
    "于是", "如此", "而今", "然后", "后来", "之后", "之前",
    # 其他常见误匹配
    "心中", "心头", "心头一", "心中一", "心中暗",
    "自然", "果然", "竟然", "居然", "忽然", "突然", "猛然",
    "似乎", "好像", "仿佛", "犹如", "宛如",
    "正是", "就是", "不是", "乃是", "本是", "原是",
    "为何不", "如何不", "怎么不",
}

# 名字末尾的常见动词/副词字（用于剥离"林渊问"→"林渊"）
# 注意：只剥离长度 >=3 的名字末尾，避免把"张问"这种真名误剥
TRAILING_VERB_CHARS = {
    "问", "笑", "怒", "叹", "答", "喊", "惊", "冷", "大", "低",
    "高", "缓", "猛", "怔", "愣", "又", "也", "却", "便", "才",
    "正", "已", "将", "欲", "即",
    # 新增：避免"不知"被识别
    "知", "有", "无", "是", "的", "了", "着", "过",
    # "心中道"等
    "心", "头", "上", "下", "里", "中",
}

# 名字末尾的代词/通用称呼字（用于剥离"便有人"→"便有"→继续剥离）
# 仅在长度 >=3 时剥离，避免误伤"陈人"等2字名
TRAILING_PRONOUN_CHARS = {
    # 代词
    "人", "他", "她", "我", "你", "它",
    # 通用称呼末尾
    "者", "生", "子", "师", "弟", "姐", "妹", "兄", "父", "母",
    "师", "尊", "道", "僧", "翁", "妪", "女", "男", "妇", "夫",
}

# 名字末尾的武器/物品字（用于剥离"着眉尖刀"→继续剥离）
# 这些字出现在名字末尾几乎一定是物品，不是人名
TRAILING_OBJECT_CHARS = {
    "刀", "剑", "枪", "棍", "棒", "鞭", "锏", "锤", "斧", "钺",
    "弓", "箭", "弩", "盾", "甲", "盔", "袍", "衣", "裳", "冠",
    "瓶", "壶", "杯", "盏", "盘", "碗", "鼎", "炉", "镜", "珠",
    "玉", "石", "金", "银", "铜", "铁", "木", "竹",
}

# 名字中包含的"非人物"子串（用于过滤"于是便有人""似乎有人"等）
# 即使剥离后，如果名字中仍包含这些子串，就过滤掉
NON_HUMAN_SUBSTRINGS = {
    "有人", "无人", "某人", "一人", "众人", "二人", "几人",
    "不知", "不能", "不会", "不是", "没有",
    "于是", "然后", "之后", "之前", "似乎", "好像", "仿佛", "犹如",
    "心中", "心头",
    "为什么", "为什么", "难道", "莫非", "哪怕",
}

# 残留过滤：2字名字如果以这些字开头或结尾，过滤掉
# 这些字单独出现时几乎不可能是人名的一部分
RESIDUAL_PREFIX_CHARS = {
    "是", "便", "着", "了", "过", "的", "地", "得",
    "也", "都", "还", "又", "再", "已", "正", "将",
    "于", "在", "向", "为", "与", "和", "或",
}
RESIDUAL_SUFFIX_CHARS = {
    "着", "了", "过", "的", "地", "得",
    "便", "也", "都", "还", "又", "再",
    "于", "在", "向", "为", "与", "和", "或",
    "上", "下", "里", "中", "前", "后",
}
