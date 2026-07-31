"""
[v1.5 第一期] 世界事件 / 玩家事件总线

设计要点：
  - GameEvent 统一结构，区分 category（player / world）
  - PlayerEventBus：NPC 主动找玩家的事件队列
  - WorldEventBus：世界级公告（战争/天灾/发现/换届）
  - 持久化到 saves/{world_id}/state/events.json（原子写入）
  - 支持过期清理（expire_old）和状态流转（pending→accepted/rejected/expired/missed）

不依赖 LLM：摘要由 WorldTick 用模板生成。
"""
from __future__ import annotations
import json
import uuid
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from .data.safe_io import load_json_safe, atomic_write_json

logger = logging.getLogger("chronoverse.world_event")


@dataclass
class GameEvent:
    """统一事件结构（玩家事件/世界事件共用）"""
    event_id: str = ""
    category: str = "player"            # "player" | "world"
    priority: str = "normal"            # "urgent" | "important" | "normal"
    event_type: str = ""                # visit/faction/disaster/rumor/provoke/...
    title: str = ""                     # 一句话标题
    summary: str = ""                   # 2-3 句摘要（模板生成，不调 LLM）
    source_npc: Optional[str] = None    # 玩家事件必填；世界事件为 None
    trigger_day: int = 0                # 触发日（游戏内天数）
    expire_day: int = 0                 # 过期日
    status: str = "pending"             # pending/accepted/rejected/expired/missed
    payload: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.event_id:
            self.event_id = f"evt_{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "GameEvent":
        return cls(
            event_id=data.get("event_id", ""),
            category=data.get("category", "player"),
            priority=data.get("priority", "normal"),
            event_type=data.get("event_type", ""),
            title=data.get("title", ""),
            summary=data.get("summary", ""),
            source_npc=data.get("source_npc"),
            trigger_day=data.get("trigger_day", 0),
            expire_day=data.get("expire_day", 0),
            status=data.get("status", "pending"),
            payload=data.get("payload", {}) or {},
        )

    def is_expired(self, current_day: int) -> bool:
        return current_day > self.expire_day and self.status == "pending"


class _BaseEventBus:
    """事件队列基类：PlayerEventBus / WorldEventBus 共用逻辑"""

    # [v1.5 第二期] 归档容量上限（仅内存，重启清空）
    ARCHIVE_MAX = 2000

    def __init__(self, persist_path: Path):
        self.persist_path = Path(persist_path)
        self.events: list[GameEvent] = []
        # [v1.5 第二期] 归档事件（仅内存，重启清空）
        # 已 accepted/rejected/expired/missed 的事件从 events 移到 archive
        self.archive: list[GameEvent] = []

    def add(self, event: GameEvent) -> None:
        self.events.append(event)
        self._save()

    def find(self, event_id: str) -> Optional[GameEvent]:
        for e in self.events:
            if e.event_id == event_id:
                return e
        # [v1.5 第二期] 归档区也查
        for e in self.archive:
            if e.event_id == event_id:
                return e
        return None

    def list_all(self) -> list[GameEvent]:
        return list(self.events)

    def list_pending(self) -> list[GameEvent]:
        """所有未处理（pending）的事件"""
        return [e for e in self.events if e.status == "pending"]

    def list_today(self, current_day: int) -> list[GameEvent]:
        """今日触发的事件（trigger_day == current_day）"""
        return [e for e in self.events
                if e.trigger_day == current_day and e.status == "pending"]

    def mark(self, event_id: str, status: str) -> bool:
        """更新事件状态，返回是否成功

        [v1.5 第二期] 终态事件（accepted/rejected/expired/missed）从 events 移到 archive
        """
        evt = self.find(event_id)
        if not evt:
            return False
        evt.status = status
        # 终态事件归档
        if status in ("accepted", "rejected", "expired", "missed"):
            if evt in self.events:
                self.events.remove(evt)
            self.archive.append(evt)
            # 容量控制：保留最近 ARCHIVE_MAX 条
            if len(self.archive) > self.ARCHIVE_MAX:
                self.archive = self.archive[-self.ARCHIVE_MAX:]
        self._save()
        return True

    def expire_old(self, current_day: int) -> int:
        """清理过期事件（pending 且超过 expire_day 的标为 expired），返回清理数量

        [v1.5 第二期] 过期事件归档到 archive
        """
        count = 0
        to_archive: list[GameEvent] = []
        for e in self.events:
            if e.is_expired(current_day):
                e.status = "expired"
                to_archive.append(e)
                count += 1
        if to_archive:
            for e in to_archive:
                self.events.remove(e)
            self.archive.extend(to_archive)
            # 容量控制
            if len(self.archive) > self.ARCHIVE_MAX:
                self.archive = self.archive[-self.ARCHIVE_MAX:]
            self._save()
        return count

    def clear(self) -> None:
        """清空所有事件（用于新游戏/读档前）"""
        self.events = []
        # [v1.5 第二期] clear 不清 archive（archive 是历史回看用，新游戏才清）
        self._save()

    def clear_archive(self) -> None:
        """清空归档（仅新游戏时调用）"""
        self.archive = []

    def list_archive(self, category: str = None, event_type: str = None,
                     day_start: int = None, day_end: int = None,
                     limit: int = 100) -> list[GameEvent]:
        """[v1.5 第二期] 查询归档事件（支持多维筛选）

        Args:
            category: 事件类别（player/world），None=全部
            event_type: 事件类型（visit/war/...），None=全部
            day_start: 起始日（含），None=不限
            day_end: 结束日（含），None=不限
            limit: 最多返回条数（按 trigger_day 倒序）
        """
        result = list(self.archive)
        if category:
            result = [e for e in result if e.category == category]
        if event_type:
            result = [e for e in result if e.event_type == event_type]
        if day_start is not None:
            result = [e for e in result if e.trigger_day >= day_start]
        if day_end is not None:
            result = [e for e in result if e.trigger_day <= day_end]
        # 按 trigger_day 倒序
        result.sort(key=lambda e: e.trigger_day, reverse=True)
        return result[:limit]

    def archive_stats(self) -> dict:
        """[v1.5 第二期] 归档统计"""
        from collections import Counter
        type_counts = Counter(e.event_type for e in self.archive)
        status_counts = Counter(e.status for e in self.archive)
        return {
            "total": len(self.archive),
            "by_type": dict(type_counts),
            "by_status": dict(status_counts),
        }

    def to_dict(self) -> list[dict]:
        return [e.to_dict() for e in self.events]

    def load_from_dict(self, data: list[dict]) -> None:
        self.events = [GameEvent.from_dict(d) for d in (data or [])]
        # 不立即 _save，避免读档时覆盖

    def _save(self) -> None:
        """原子写入持久化"""
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.persist_path, self.to_dict())
        except Exception as e:
            logger.warning("Failed to persist events to %s: %s", self.persist_path, e)

    def load_from_disk(self) -> None:
        """从磁盘加载（读档时调用）"""
        data = load_json_safe(self.persist_path, default=[]) or []
        self.load_from_dict(data)


class PlayerEventBus(_BaseEventBus):
    """玩家事件队列（NPC 主动找你）"""
    pass


class WorldEventBus(_BaseEventBus):
    """世界事件队列（公告类）"""
    pass


# ===== 事件模板库（不调 LLM，纯模板） =====

# 玩家事件模板：按好感度分桶
PLAYER_EVENT_TEMPLATES = {
    "high_favor": [   # favor >= 70
        ("visit",      "important", "{name}前来拜访，说有要事相商"),
        ("invite",     "important", "{name}邀你一同外出，似乎有什么打算"),
        ("gift",       "normal",    "{name}带了礼物前来，说是心意"),
        ("chat",       "normal",    "{name}路过门前，进来与你叙旧"),
    ],
    "neutral": [      # 30 <= favor < 70
        ("greet",      "normal",    "{name}路过你家门口，进来打个招呼"),
        ("ask_help",   "important", "{name}神色焦急，似乎需要帮助"),
        ("deliver_msg","normal",    "{name}带来一个消息，特意跑来相告"),
    ],
    "enemy": [        # favor < 30 且有"仇人"tag
        ("provoke",    "urgent",    "{name}上门挑衅，态度不善"),
        ("challenge",  "urgent",    "{name}送来战书，约定时辰一决高下"),
        ("threaten",   "urgent",    "{name}登门威胁，扬言让你好看"),
    ],
    "low_favor": [    # favor < 30 且非仇人（陌生人冷遇）
        ("cold_visit", "normal",    "{name}有事路过，顺道拜访"),
    ],
}

# 世界事件模板：每日 10% 概率触发 1 条
WORLD_EVENT_TEMPLATES = [
    ("war",           "两国开战",   "边境传来急报，两国已正式开战，民间人心惶惶"),
    ("beast_tide",    "兽潮将至",   "前方探子回报，兽潮即将席卷此地，各地戒备森严"),
    ("discovery",     "新星降临",   "夜空天象有异，似有新星降临，术士议论纷纷"),
    ("faction_change","门派换届",   "江湖传闻，某大派即将换届，引发势力洗牌"),
    ("drought",       "大旱成灾",   "多地数月无雨，庄稼枯死，粮价飞涨"),
    ("epidemic",      "瘟疫蔓延",   "南方数城爆发瘟疫，朝廷下令封城"),
    ("treasure",      "异宝现世",   "传闻某地有异宝现世，引得各方势力云集"),
    ("rebellion",     "民变四起",   "苛政之下，多处百姓揭竿而起，天下震动"),
]


def pick_player_template(favorability: int, is_enemy: bool) -> tuple[str, str, str]:
    """根据好感度和是否仇人挑选模板，返回 (event_type, priority, template_str)"""
    import random
    if is_enemy:
        bucket = PLAYER_EVENT_TEMPLATES["enemy"]
    elif favorability >= 70:
        bucket = PLAYER_EVENT_TEMPLATES["high_favor"]
    elif favorability >= 30:
        bucket = PLAYER_EVENT_TEMPLATES["neutral"]
    else:
        bucket = PLAYER_EVENT_TEMPLATES["low_favor"]
    return random.choice(bucket)


def pick_world_template() -> tuple[str, str, str]:
    """随机挑选世界事件模板，返回 (event_type, title, summary)"""
    import random
    return random.choice(WORLD_EVENT_TEMPLATES)
