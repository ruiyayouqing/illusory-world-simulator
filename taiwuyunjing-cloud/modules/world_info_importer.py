"""SillyTavern World Info 导入器：兼容酒馆世界书格式。"""
from __future__ import annotations
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("chronoverse.world_info")


@dataclass
class WorldInfoEntry:
    """世界书条目（兼容 SillyTavern 格式）。"""
    uid: int
    keys: list[str]  # 主关键词
    keys_secondary: list[str]  # 次要关键词
    content: str  # 注入内容
    comment: str = ""  # 描述
    constant: bool = False  # 是否常驻（不需要关键词触发）
    selective: bool = False  # 是否选择性触发（需要主+次关键词）
    selective_logic: int = 0  # 选择逻辑：0=AND, 1=OR, 2=NOT
    order: int = 100  # 优先级（数字越小越优先）
    position: int = 0  # 注入位置：0=before_char, 1=after_char, 2=before_an, 3=after_an
    disable: bool = False  # 是否禁用
    probability: int = 100  # 触发概率（0-100）
    depth: int = 4  # 递归深度
    group: str = ""  # 分组
    group_weight: int = 100  # 分组权重
    case_sensitive: bool = False  # 大小写敏感
    match_whole_words: bool = False  # 全词匹配

    def to_dict(self) -> dict:
        return {
            "uid": self.uid,
            "keys": self.keys,
            "keys_secondary": self.keys_secondary,
            "content": self.content,
            "comment": self.comment,
            "constant": self.constant,
            "selective": self.selective,
            "selective_logic": self.selective_logic,
            "order": self.order,
            "position": self.position,
            "disable": self.disable,
            "probability": self.probability,
            "depth": self.depth,
            "group": self.group,
            "group_weight": self.group_weight,
            "case_sensitive": self.case_sensitive,
            "match_whole_words": self.match_whole_words,
        }


@dataclass
class WorldInfoBook:
    """世界书。"""
    name: str
    entries: list[WorldInfoEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "entries": [e.to_dict() for e in self.entries],
        }


class WorldInfoImporter:
    """SillyTavern World Info 导入器。"""

    def import_from_file(self, file_path: str | Path) -> WorldInfoBook:
        """从文件导入世界书。"""
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"世界书文件不存在: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return self.import_from_dict(data)

    def import_from_dict(self, data: dict) -> WorldInfoBook:
        """从字典导入世界书。"""
        # 提取名称
        name = ""
        if "originalData" in data:
            name = data["originalData"].get("name", "")
        if not name:
            name = data.get("name", "导入的世界书")

        # 解析条目
        entries = []
        entries_data = data.get("entries", {})

        for uid_str, entry_data in entries_data.items():
            try:
                entry = self._parse_entry(entry_data)
                if entry:
                    entries.append(entry)
            except Exception as e:
                logger.warning("Failed to parse world info entry %s: %s", uid_str, e)

        logger.info("Imported world info '%s': %d entries", name, len(entries))
        return WorldInfoBook(name=name, entries=entries)

    def _parse_entry(self, data: dict) -> WorldInfoEntry | None:
        """解析单个条目。"""
        if data.get("disable", False):
            return None

        # 解析关键词
        keys = data.get("key", [])
        if isinstance(keys, str):
            keys = [k.strip() for k in keys.split(",") if k.strip()]

        keys_secondary = data.get("keysecondary", [])
        if isinstance(keys_secondary, str):
            keys_secondary = [k.strip() for k in keys_secondary.split(",") if k.strip()]

        content = data.get("content", "")
        if not content:
            return None

        return WorldInfoEntry(
            uid=data.get("uid", 0),
            keys=keys,
            keys_secondary=keys_secondary,
            content=content,
            comment=data.get("comment", ""),
            constant=data.get("constant", False),
            selective=data.get("selective", False),
            selective_logic=data.get("selectiveLogic", 0),
            order=data.get("order", 100),
            position=data.get("position", 0),
            disable=data.get("disable", False),
            probability=data.get("probability", 100),
            depth=data.get("depth", 4),
            group=data.get("group", ""),
            group_weight=data.get("groupWeight", 100),
            case_sensitive=data.get("caseSensitive", False),
            match_whole_words=data.get("matchWholeWords", False),
        )

    def to_lorebook_format(self, book: WorldInfoBook) -> list[dict]:
        """
        将世界书转换为 太虚幻境 Lorebook 格式。
        返回 LorebookEntry 字典列表。
        """
        lorebook_entries = []
        for entry in book.entries:
            # 转换为 Lorebook 格式
            lorebook_entry = {
                "id": f"wi_{entry.uid}",
                "keywords": entry.keys,
                "secondary_keywords": entry.keys_secondary,
                "content": entry.content,
                "comment": entry.comment,
                "constant": entry.constant,
                "selective": entry.selective,
                "selective_logic": entry.selective_logic,
                "priority": entry.order,
                "position": entry.position,
                "probability": entry.probability,
                "enabled": not entry.disable,
                "case_sensitive": entry.case_sensitive,
                "match_whole_words": entry.match_whole_words,
                "group": entry.group,
                "group_weight": entry.group_weight,
            }
            lorebook_entries.append(lorebook_entry)
        return lorebook_entries

    def export_to_lorebook(self, book: WorldInfoBook, output_path: str | Path):
        """导出为 Lorebook JSON 文件。"""
        entries = self.to_lorebook_format(book)
        output = {
            "name": book.name,
            "source": "sillytavern",
            "entries": entries,
        }
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        logger.info("Exported world info '%s' to %s", book.name, output_path)


class WorldInfoMatcher:
    """世界书匹配器：根据输入文本匹配并激活条目。"""

    def __init__(self, book: WorldInfoBook):
        self.book = book
        self._compiled_patterns: dict[int, list] = {}  # uid -> compiled patterns
        self._compile_patterns()

    def _compile_patterns(self):
        """预编译关键词正则。"""
        for entry in self.book.entries:
            patterns = []
            for key in entry.keys:
                if not key:
                    continue
                if entry.match_whole_words:
                    # 全词匹配
                    pattern = r'\b' + re.escape(key) + r'\b'
                else:
                    pattern = re.escape(key)

                flags = 0 if entry.case_sensitive else re.IGNORECASE
                patterns.append((key, re.compile(pattern, flags)))

            self._compiled_patterns[entry.uid] = patterns

    def match(self, text: str) -> list[WorldInfoEntry]:
        """
        匹配文本，返回激活的条目。
        处理 selective 逻辑、概率、分组等。
        """
        if not text:
            return []

        matched_entries = []
        activated_groups: dict[str, list[WorldInfoEntry]] = {}

        for entry in self.book.entries:
            if entry.disable:
                continue

            # 常驻条目直接激活
            if entry.constant:
                if self._check_probability(entry):
                    matched_entries.append(entry)
                continue

            # 关键词匹配
            is_matched = self._match_entry(entry, text)

            if is_matched and self._check_probability(entry):
                if entry.group:
                    # 分组条目：收集到分组
                    if entry.group not in activated_groups:
                        activated_groups[entry.group] = []
                    activated_groups[entry.group].append(entry)
                else:
                    matched_entries.append(entry)

        # 处理分组：每组只选一个（按权重随机）
        import random
        for group_name, entries in activated_groups.items():
            weights = [e.group_weight for e in entries]
            selected = random.choices(entries, weights=weights, k=1)[0]
            matched_entries.append(selected)

        # 按优先级排序
        matched_entries.sort(key=lambda e: e.order)

        return matched_entries

    def _match_entry(self, entry: WorldInfoEntry, text: str) -> bool:
        """检查条目是否匹配文本。"""
        patterns = self._compiled_patterns.get(entry.uid, [])

        if not patterns and not entry.constant:
            return False

        # 主关键词匹配
        primary_matched = any(p.search(text) for _, p in patterns)

        if not entry.selective:
            # 非选择性：主关键词匹配即可
            return primary_matched

        # 选择性：需要检查次要关键词
        secondary_matched = False
        for key in entry.keys_secondary:
            if not key:
                continue
            if entry.match_whole_words:
                pattern = r'\b' + re.escape(key) + r'\b'
            else:
                pattern = re.escape(key)
            flags = 0 if entry.case_sensitive else re.IGNORECASE
            if re.search(pattern, text, flags):
                secondary_matched = True
                break

        # 选择逻辑
        if entry.selective_logic == 0:  # AND
            return primary_matched and secondary_matched
        elif entry.selective_logic == 1:  # OR
            return primary_matched or secondary_matched
        elif entry.selective_logic == 2:  # NOT
            return primary_matched and not secondary_matched
        else:
            return primary_matched

    def _check_probability(self, entry: WorldInfoEntry) -> bool:
        """检查概率触发。"""
        if entry.probability >= 100:
            return True
        if entry.probability <= 0:
            return False
        import random
        return random.random() * 100 < entry.probability

    def get_context(self, text: str, max_entries: int = 10) -> str:
        """获取匹配的上下文文本。"""
        matched = self.match(text)[:max_entries]
        if not matched:
            return ""

        parts = [e.content for e in matched if e.content]
        return "\n\n".join(parts)
