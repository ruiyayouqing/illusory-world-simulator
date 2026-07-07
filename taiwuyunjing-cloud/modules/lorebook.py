from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("chronoverse.lorebook")


@dataclass
class LorebookEntry:
    """世界书条目"""
    uid: str = ""
    keywords: list[str] = field(default_factory=list)
    content: str = ""
    entry_type: str = "keyword"
    priority: int = 0
    enabled: bool = True
    position: str = "before_main"
    selective: bool = False
    secondary_key: list[str] = field(default_factory=list)
    regex: str = ""
    constant: bool = False
    dependencies: list[str] = field(default_factory=list)
    depth: int = 0
    # [v10+] SillyTavern World Info 兼容字段
    selective_logic: int = 0  # 选择逻辑：0=AND, 1=OR, 2=NOT
    probability: int = 100  # 触发概率（0-100）
    case_sensitive: bool = False  # 大小写敏感
    match_whole_words: bool = False  # 全词匹配
    group: str = ""  # 分组（同组按权重随机选一个）
    group_weight: int = 100  # 分组权重
    comment: str = ""  # 条目描述


class Lorebook:
    """增强版世界书：关键词/正则触发 + 条件激活 + 条目依赖 + 深度注入"""

    def __init__(self):
        self.entries: dict[str, LorebookEntry] = {}
        self.global_entries: list[LorebookEntry] = []
        self._uid_counter = 0
        self._active_cache: dict[str, str] = {}
        self._last_trigger_text: str = ""

    def _next_uid(self) -> str:
        self._uid_counter += 1
        return f"entry_{self._uid_counter}"

    def add_entry(self, keywords: list[str], content: str, entry_type: str = "keyword",
                  priority: int = 0, enabled: bool = True, position: str = "before_main",
                  selective: bool = False, secondary_key: list[str] = None,
                  regex: str = "", constant: bool = False, dependencies: list[str] = None,
                  uid: str = "", depth: int = 0,
                  selective_logic: int = 0, probability: int = 100,
                  case_sensitive: bool = False, match_whole_words: bool = False,
                  group: str = "", group_weight: int = 100,
                  comment: str = "") -> str:
        entry_uid = uid or self._next_uid()
        entry = LorebookEntry(
            uid=entry_uid, keywords=keywords, content=content,
            entry_type=entry_type, priority=priority, enabled=enabled,
            position=position, selective=selective,
            secondary_key=secondary_key or [], regex=regex,
            constant=constant, dependencies=dependencies or [], depth=depth,
            selective_logic=selective_logic, probability=probability,
            case_sensitive=case_sensitive, match_whole_words=match_whole_words,
            group=group, group_weight=group_weight, comment=comment,
        )
        self.entries[entry_uid] = entry
        return entry_uid

    def add_global(self, content: str, priority: int = 0):
        self.global_entries.append(LorebookEntry(
            content=content, priority=priority, constant=True,
            entry_type="global",
        ))

    def remove_entry(self, uid: str):
        self.entries.pop(uid, None)

    def update_entry(self, uid: str, **kwargs):
        if uid in self.entries:
            for k, v in kwargs.items():
                if hasattr(self.entries[uid], k):
                    setattr(self.entries[uid], k, v)

    def init_default_entries(self, world_type: str, npc_states: dict = None):
        self.entries.clear()
        self.global_entries.clear()

        self.add_global(
            "你必须严格遵守已建立的世界观设定。NPC身份、职业一旦设定，不可无理由更改。"
            "但如果NPC列表中明确标注了身份变更历史，则以最新身份为准。",
            priority=100,
        )

        world_rules = {
            "historical": "这是一个真实历史世界。没有魔法、修仙、现代科技。角色的行为必须符合历史背景。",
            "wuxia": "这是一个武侠世界。有内力武功、轻功、点穴。没有魔法枪械现代科技。",
            "xianxia": "这是一个修仙世界。有灵气、法宝、阵法、丹药。没有现代科技。",
            "fantasy": "这是一个奇幻世界。有魔法、种族、神器。根据具体设定判断。",
            "scifi": "这是一个科幻世界。有高科技、AI、飞船。根据具体时代判断。",
            "postapocalyptic": "这是一个末日世界。文明崩塌，资源稀缺，弱肉强食。",
            "modern": "这是一个现代世界。当代社会规则适用。",
        }
        if world_type in world_rules:
            self.add_global(world_rules[world_type], priority=90)

        if npc_states:
            for nid, npc in npc_states.items():
                summary = npc.get_identity_summary() if hasattr(npc, 'get_identity_summary') else \
                          f"{npc.name}是一个{npc.personality or '普通人'}"
                self.add_entry(
                    keywords=[npc.name], content=summary,
                    entry_type="npc", priority=80, uid=f"npc_{nid}",
                )

    def update_npc_entry(self, npc_name: str, new_identity_summary: str):
        for uid, entry in self.entries.items():
            if npc_name in entry.keywords and entry.entry_type == "npc":
                entry.content = new_identity_summary
                return
        self.add_entry(
            keywords=[npc_name], content=new_identity_summary,
            entry_type="npc", priority=80,
        )

    def match(self, text: str) -> dict[str, list[str]]:
        """匹配世界书条目，返回按位置分组的注入内容。

        [v10+] 增强：支持 selective_logic (AND/OR/NOT)、概率触发、
        分组（每组按权重随机选一个）、大小写敏感、全词匹配。
        兼容 SillyTavern World Info 语义。
        """
        import random

        result: dict[str, list[str]] = {
            "before_main": [],
            "after_main": [],
            "depth_inject": [],
        }

        for entry in self.global_entries:
            if entry.constant:
                pos = entry.position or "before_main"
                result.setdefault(pos, []).append(entry.content)

        self._last_trigger_text = text
        activated: set[str] = set()

        text_lower = text.lower()

        # [v10+] 分组条目收集：同组按权重随机选一个
        grouped_entries: dict[str, list[LorebookEntry]] = {}

        for uid, entry in self.entries.items():
            if not entry.enabled and not entry.constant:
                continue

            if entry.constant:
                activated.add(uid)
                # [v10+] 常驻条目注入内容（兼容世界书 constant 语义）
                if self._check_probability(entry):
                    pos = entry.position or "before_main"
                    result.setdefault(pos, []).append(entry.content)
                continue

            if entry.dependencies:
                if not all(d in activated for d in entry.dependencies):
                    continue

            matched = self._match_keywords(entry, text, text_lower)

            if matched and self._check_probability(entry):
                activated.add(uid)
                if entry.group:
                    # 分组条目：收集到分组，稍后按权重选择
                    grouped_entries.setdefault(entry.group, []).append(entry)
                else:
                    pos = entry.position or "before_main"
                    result.setdefault(pos, []).append(entry.content)

        # [v10+] 分组处理：每组按权重随机选一个
        for group_name, g_entries in grouped_entries.items():
            weights = [e.group_weight for e in g_entries]
            selected = random.choices(g_entries, weights=weights, k=1)[0]
            pos = selected.position or "before_main"
            result.setdefault(pos, []).append(selected.content)

        for uid in activated:
            if uid in self.entries:
                self._active_cache[uid] = self.entries[uid].content

        return result

    def _match_keywords(self, entry: LorebookEntry, text: str,
                        text_lower: str) -> bool:
        """[v10+] 检查条目关键词是否匹配文本，支持大小写敏感、全词匹配、
        selective_logic (AND/OR/NOT)。"""
        # 主关键词匹配
        primary_matched = False
        for kw in entry.keywords:
            if not kw:
                continue
            if self._keyword_in_text(kw, text, text_lower, entry):
                primary_matched = True
                break

        if not primary_matched and entry.regex:
            try:
                if re.search(entry.regex, text):
                    primary_matched = True
            except re.error:
                pass

        if not primary_matched:
            return False

        # 非选择性：主关键词匹配即可
        if not entry.selective or not entry.secondary_key:
            return True

        # 选择性：检查次要关键词
        secondary_matched = any(
            self._keyword_in_text(sk, text, text_lower, entry)
            for sk in entry.secondary_key if sk
        )

        # 选择逻辑：0=AND, 1=OR, 2=NOT
        logic = entry.selective_logic
        if logic == 1:  # OR
            return primary_matched or secondary_matched
        elif logic == 2:  # NOT
            return primary_matched and not secondary_matched
        else:  # AND（默认，兼容旧行为）
            return primary_matched and secondary_matched

    @staticmethod
    def _keyword_in_text(keyword: str, text: str, text_lower: str,
                         entry: LorebookEntry) -> bool:
        """[v10+] 检查单个关键词是否出现在文本中，支持大小写敏感和全词匹配。"""
        if entry.match_whole_words:
            pattern = r'\b' + re.escape(keyword) + r'\b'
            flags = 0 if entry.case_sensitive else re.IGNORECASE
            return re.search(pattern, text, flags) is not None
        if entry.case_sensitive:
            return keyword in text
        return keyword.lower() in text_lower

    @staticmethod
    def _check_probability(entry: LorebookEntry) -> bool:
        """[v10+] 检查概率触发（兼容世界书 probability 语义）。"""
        if entry.probability >= 100:
            return True
        if entry.probability <= 0:
            return False
        import random
        return random.random() * 100 < entry.probability

    def match_count(self, text: str) -> int:
        """返回匹配的条目数量（用于 token 预算估算）"""
        result = self.match(text)
        return sum(len(v) for v in result.values())

    def to_prompt(self, text: str) -> str:
        matched = self.match(text)
        parts = []
        for pos in ["before_main", "after_main", "depth_inject"]:
            items = matched.get(pos, [])
            if items:
                parts.append(
                    f"【世界书设定（必须严格遵守）】\n"
                    + "\n".join([f"- {m}" for m in items])
                )
        return "\n".join(parts) + "\n" if parts else ""

    def to_dict(self) -> dict:
        entries_dict = {}
        for uid, entry in self.entries.items():
            entries_dict[uid] = {
                "uid": entry.uid,
                "keywords": entry.keywords,
                "content": entry.content,
                "entry_type": entry.entry_type,
                "priority": entry.priority,
                "enabled": entry.enabled,
                "position": entry.position,
                "selective": entry.selective,
                "secondary_key": entry.secondary_key,
                "regex": entry.regex,
                "constant": entry.constant,
                "dependencies": entry.dependencies,
                "depth": entry.depth,
                "selective_logic": entry.selective_logic,
                "probability": entry.probability,
                "case_sensitive": entry.case_sensitive,
                "match_whole_words": entry.match_whole_words,
                "group": entry.group,
                "group_weight": entry.group_weight,
                "comment": entry.comment,
            }
        return {
            "entries": entries_dict,
            "global_entries": [
                {"content": e.content, "priority": e.priority}
                for e in self.global_entries
            ],
        }

    def from_dict(self, data: dict):
        self.entries.clear()
        self.global_entries.clear()
        # [v10+] 兼容旧存档：过滤未知字段，避免 LorebookEntry 构造失败
        valid_fields = {f.name for f in LorebookEntry.__dataclass_fields__.values()}
        for uid, ed in data.get("entries", {}).items():
            filtered = {k: v for k, v in ed.items() if k in valid_fields}
            self.entries[uid] = LorebookEntry(**filtered)
        for ged in data.get("global_entries", []):
            self.global_entries.append(LorebookEntry(
                content=ged["content"], priority=ged.get("priority", 0),
                constant=True, entry_type="global",
            ))

    def import_from_world_info(self, book) -> int:
        """[v10+] 从 SillyTavern 世界书导入条目。

        将 WorldInfoBook 中的条目转换为 Lorebook 内部格式并添加。
        常驻条目（constant=True）加入 global_entries，其余加入 entries。
        不影响现有 Lorebook 条目（追加模式）。

        Args:
            book: WorldInfoBook 实例

        Returns:
            导入的条目数量
        """
        from .world_info_importer import WorldInfoImporter
        importer = WorldInfoImporter()
        entries = importer.to_lorebook_format(book)
        # WorldInfo position (int) -> Lorebook position (str)
        pos_map = {
            0: "before_main",  # before_char
            1: "after_main",   # after_char
            2: "before_main",  # before_an
            3: "after_main",   # after_an
        }
        count = 0
        for ed in entries:
            position = pos_map.get(ed.get("position", 0), "before_main")
            if ed.get("constant", False):
                # 常驻条目加入 global_entries，始终注入
                self.add_global(
                    ed.get("content", ""),
                    priority=ed.get("priority", 100),
                )
            else:
                self.add_entry(
                    keywords=ed.get("keywords", []),
                    content=ed.get("content", ""),
                    entry_type="world_info",
                    priority=ed.get("priority", 100),
                    enabled=ed.get("enabled", True),
                    position=position,
                    selective=ed.get("selective", False),
                    secondary_key=ed.get("secondary_keywords", []),
                    constant=False,
                    uid=ed.get("id", ""),
                    selective_logic=ed.get("selective_logic", 0),
                    probability=ed.get("probability", 100),
                    case_sensitive=ed.get("case_sensitive", False),
                    match_whole_words=ed.get("match_whole_words", False),
                    group=ed.get("group", ""),
                    group_weight=ed.get("group_weight", 100),
                    comment=ed.get("comment", ""),
                )
            count += 1
        logger.info("Imported %d entries from world info '%s'", count, book.name)
        return count
