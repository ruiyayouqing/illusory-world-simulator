"""
[v10+] 伏笔生命周期管理器 — 借鉴 InkOS 的 Hook 系统

核心语义：
  - insert:   插入新伏笔（叙事中埋下线索）
  - mention:  在后续叙事中提及（强化记忆，但未解决）
  - resolve:  解决伏笔（真相揭晓、线索收回）
  - defer:    推迟处理（暂时搁置，标记原因）

健康检查：
  - stale debt:  超过 N 天未提及/解决的伏笔
  - burst:       同时打开的伏笔过多
  - orphan:      被提及但从未被插入的伏笔（数据异常）
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .db.chroma_db import MemoryStore

logger = logging.getLogger("chronoverse.foreshadow")


class HookStatus:
    ACTIVE = "active"         # 已插入，等待发展
    MENTIONED = "mentioned"   # 已被提及，仍在活跃
    RESOLVED = "resolved"     # 已解决
    DEFERRED = "deferred"     # 已推迟
    STALE = "stale"           # 过期未处理（自动标记）


@dataclass
class ForeshadowHook:
    """一个伏笔钩子的完整生命周期记录"""
    hook_id: str
    content: str              # 伏笔内容
    status: str = HookStatus.ACTIVE
    importance: str = "normal"  # low / normal / high / critical
    inserted_day: int = 0
    inserted_turn: int = 0
    last_mentioned_day: int = 0
    mention_count: int = 0
    resolved_day: int = 0
    resolution: str = ""      # 解决方式描述
    defer_reason: str = ""    # 推迟原因
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "hook_id": self.hook_id,
            "content": self.content,
            "status": self.status,
            "importance": self.importance,
            "inserted_day": self.inserted_day,
            "inserted_turn": self.inserted_turn,
            "last_mentioned_day": self.last_mentioned_day,
            "mention_count": self.mention_count,
            "resolved_day": self.resolved_day,
            "resolution": self.resolution,
            "defer_reason": self.defer_reason,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ForeshadowHook":
        from .schemas import safe_dataclass_from_dict
        return safe_dataclass_from_dict(cls, d)


class ForeshadowLifecycle:
    """
    [v10+] 伏笔生命周期管理器

    管理伏笔从插入到解决的完整生命周期。
    与 ChromaDB 的 foreshadow_collection 配合，
    在其之上增加了状态管理和健康检查。
    """

    # 健康检查阈值
    STALE_THRESHOLD_DAYS = 30    # 超过 30 天未提及视为过期
    BURST_THRESHOLD = 8          # 同时活跃伏笔超过 8 个视为过多
    WARN_BURST_THRESHOLD = 12    # 超过 12 个发出警告

    # 提醒模式
    REMINDER_NORMAL = "normal"   # 正常模式：活跃伏笔注入到叙事 prompt
    REMINDER_SILENT = "silent"   # 静默模式：后台运行，不注入 prompt，不提醒

    def __init__(self):
        self.hooks: dict[str, ForeshadowHook] = {}  # hook_id -> ForeshadowHook
        self._hook_counter = 0
        self._health_history: list[dict] = []
        self.reminder_mode: str = self.REMINDER_NORMAL  # 默认正常模式

    def insert(self, content: str, day: int, turn: int,
               importance: str = "normal",
               tags: list[str] = None,
               memory: "MemoryStore" = None) -> ForeshadowHook:
        """
        插入一个新伏笔。

        同时写入 ChromaDB（用于向量检索）和本地 hooks 字典（用于状态管理）。
        """
        # 检查是否已存在高度相似的伏笔
        for hook in self.hooks.values():
            if hook.status in (HookStatus.ACTIVE, HookStatus.MENTIONED):
                if self._text_similarity(hook.content, content) > 0.7:
                    # 已存在类似伏笔，更新而非新增
                    hook.mention_count += 1
                    hook.last_mentioned_day = day
                    if hook.status == HookStatus.ACTIVE:
                        hook.status = HookStatus.MENTIONED
                    return hook

        self._hook_counter += 1
        hook_id = f"hook_{self._hook_counter:04d}"

        hook = ForeshadowHook(
            hook_id=hook_id,
            content=content[:500],
            status=HookStatus.ACTIVE,
            importance=importance,
            inserted_day=day,
            inserted_turn=turn,
            last_mentioned_day=day,
            tags=tags or [],
        )
        self.hooks[hook_id] = hook

        # 同步写入 ChromaDB（保持向量检索能力）
        if memory:
            memory.add_foreshadow(content, day, importance)

        logger.info("Foreshadow inserted: %s [%s] day=%d", hook_id, importance, day)
        return hook

    def mention(self, hook_id: str, day: int):
        """标记一个伏笔在叙事中被提及"""
        hook = self.hooks.get(hook_id)
        if not hook:
            return
        if hook.status in (HookStatus.RESOLVED,):
            return  # 已解决的伏笔不再追踪提及
        hook.mention_count += 1
        hook.last_mentioned_day = day
        if hook.status == HookStatus.ACTIVE:
            hook.status = HookStatus.MENTIONED
        if hook.status == HookStatus.STALE:
            hook.status = HookStatus.MENTIONED  # 被提及后从过期恢复

    def resolve(self, hook_id: str, day: int, resolution: str = ""):
        """标记一个伏笔已解决"""
        hook = self.hooks.get(hook_id)
        if not hook:
            return
        hook.status = HookStatus.RESOLVED
        hook.resolved_day = day
        hook.resolution = resolution
        logger.info("Foreshadow resolved: %s at day %d", hook_id, day)

    def defer(self, hook_id: str, day: int, reason: str = ""):
        """标记一个伏笔被推迟处理"""
        hook = self.hooks.get(hook_id)
        if not hook:
            return
        hook.status = HookStatus.DEFERRED
        hook.defer_reason = reason
        hook.last_mentioned_day = day

    def check_stale(self, current_day: int) -> list[ForeshadowHook]:
        """
        检测过期未处理的伏笔（stale debt）。

        返回超过 STALE_THRESHOLD_DAYS 天未提及且仍活跃的伏笔列表。
        """
        stale = []
        for hook in self.hooks.values():
            if hook.status not in (HookStatus.ACTIVE, HookStatus.MENTIONED):
                continue
            days_since_mention = current_day - hook.last_mentioned_day
            if days_since_mention > self.STALE_THRESHOLD_DAYS:
                hook.status = HookStatus.STALE
                stale.append(hook)

        if stale:
            logger.info("Stale foreshadows detected: %d hooks", len(stale))

        return stale

    def check_burst(self) -> dict:
        """
        检测伏笔是否过多（burst）。

        返回 {burst: bool, active_count: int, warning: str}
        """
        active_count = sum(
            1 for h in self.hooks.values()
            if h.status in (HookStatus.ACTIVE, HookStatus.MENTIONED)
        )

        result = {
            "burst": active_count >= self.BURST_THRESHOLD,
            "active_count": active_count,
            "threshold": self.BURST_THRESHOLD,
            "warning": "",
        }

        if active_count >= self.WARN_BURST_THRESHOLD:
            result["warning"] = (
                f"当前有 {active_count} 个活跃伏笔，数量过多。"
                f"建议优先解决旧伏笔，避免读者遗忘。"
            )
        elif active_count >= self.BURST_THRESHOLD:
            result["warning"] = (
                f"当前有 {active_count} 个活跃伏笔，接近上限。"
            )

        return result

    def get_active_hooks(self) -> list[dict]:
        """获取所有活跃伏笔，按重要性排序"""
        active = [
            h for h in self.hooks.values()
            if h.status in (HookStatus.ACTIVE, HookStatus.MENTIONED, HookStatus.STALE)
        ]
        importance_order = {"critical": 0, "high": 1, "normal": 2, "low": 3}
        active.sort(key=lambda h: importance_order.get(h.importance, 2))
        return [h.to_dict() for h in active]

    def get_resolved_hooks(self) -> list[dict]:
        """获取所有已解决的伏笔"""
        resolved = [
            h for h in self.hooks.values()
            if h.status == HookStatus.RESOLVED
        ]
        return [h.to_dict() for h in resolved]

    def get_hooks_for_prompt(self, max_hooks: int = 5) -> str:
        """
        将活跃伏笔格式化为可注入到叙事 prompt 的文本。
        提醒 LLM 在叙事中关注这些未解决的线索。

        静默模式下返回空字符串，不注入任何提醒。
        """
        if self.reminder_mode == self.REMINDER_SILENT:
            return ""

        active = [
            h for h in self.hooks.values()
            if h.status in (HookStatus.ACTIVE, HookStatus.MENTIONED, HookStatus.STALE)
        ]
        if not active:
            return ""

        # 按重要性排序
        importance_order = {"critical": 0, "high": 1, "normal": 2, "low": 3}
        active.sort(key=lambda h: importance_order.get(h.importance, 2))
        active = active[:max_hooks]

        parts = []
        for h in active:
            status_label = {
                HookStatus.ACTIVE: "新埋下",
                HookStatus.MENTIONED: "已提及",
                HookStatus.STALE: "⚠过期未解",
            }.get(h.status, h.status)
            parts.append(
                f"- [{status_label}] {h.content[:80]}"
                f"（第{h.inserted_day}天埋下，提及{h.mention_count}次）"
            )

        return "【活跃伏笔】\n" + "\n".join(parts)

    def get_health_report(self, current_day: int) -> dict:
        """生成伏笔健康报告（只读检测，不修改状态）"""
        # 只读检测过期伏笔，不调用 check_stale 以避免修改状态
        stale_candidates = [
            h for h in self.hooks.values()
            if h.status in (HookStatus.ACTIVE, HookStatus.MENTIONED)
            and current_day - h.last_mentioned_day > self.STALE_THRESHOLD_DAYS
        ]
        burst = self.check_burst()

        by_status = {}
        for h in self.hooks.values():
            by_status.setdefault(h.status, []).append(h)

        total = len(self.hooks)
        active_count = len(by_status.get(HookStatus.ACTIVE, [])) + len(by_status.get(HookStatus.MENTIONED, []))
        resolved_count = len(by_status.get(HookStatus.RESOLVED, []))
        stale_count = len(stale_candidates)

        report = {
            "total": total,
            "active": active_count,
            "resolved": resolved_count,
            "stale": stale_count,
            "deferred": len(by_status.get(HookStatus.DEFERRED, [])),
            "resolution_rate": round(resolved_count / total, 2) if total > 0 else 0,
            "burst": burst,
            "stale_hooks": [h.to_dict() for h in stale_candidates[:5]],
        }

        self._health_history.append(report)
        if len(self._health_history) > 20:
            self._health_history = self._health_history[-20:]

        return report

    def find_hook_by_content(self, text: str) -> ForeshadowHook | None:
        """根据文本内容查找匹配的伏笔"""
        best_hook = None
        best_score = 0.0
        for hook in self.hooks.values():
            if hook.status == HookStatus.RESOLVED:
                continue
            score = self._text_similarity(hook.content, text)
            if score > best_score and score > 0.4:
                best_score = score
                best_hook = hook
        return best_hook

    @staticmethod
    def _text_similarity(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        set_a = set(a)
        set_b = set(b)
        intersection = set_a & set_b
        union = set_a | set_b
        return len(intersection) / len(union) if union else 0.0

    def to_dict(self) -> dict:
        return {
            "hooks": {hid: h.to_dict() for hid, h in self.hooks.items()},
            "hook_counter": self._hook_counter,
            "health_history": self._health_history[-10:],
            "reminder_mode": self.reminder_mode,
        }

    def from_dict(self, data: dict):
        self.hooks = {
            hid: ForeshadowHook.from_dict(h)
            for hid, h in data.get("hooks", {}).items()
        }
        self._hook_counter = data.get("hook_counter", 0)
        self._health_history = data.get("health_history", [])
        self.reminder_mode = data.get("reminder_mode", self.REMINDER_NORMAL)
