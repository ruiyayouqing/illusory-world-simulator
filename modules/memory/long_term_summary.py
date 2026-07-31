"""[v1.6 P1-6] 长期记忆 LLM 摘要 + 记忆审计日志。

参考：
- Generative Agents (Stanford 2023) 的 reflection 机制：
  定期从原始记忆流生成高层洞察，作为长期记忆存储。
- MemGPT/Letta 的 memory consolidation：
  将短期工作记忆压缩为长期记忆，保留关键信息。

本模块在现有 MemoryCurator/AutonomousMemoryManager 之上增加：

1. **LongTermMemorySummarizer（长期记忆摘要器）**：
   - 生成多层摘要：单日摘要 → 周期摘要 → 里程碑摘要
   - 摘要写回 MemoryStore（向量库），使摘要成为可检索的长期记忆
   - 重要性分级：日常 0.6 / 关键剧情 0.8 / 里程碑 0.95
   - 支持 LLM 失败回退（规则提取关键句）

2. **MemoryAuditLog（记忆审计日志）**：
   - 记录每次记忆操作（创建/摘要/归档/删除/修改）
   - 线程安全的环形缓冲（保留最近 200 条）
   - 支持追溯：某条记忆何时被创建/被谁修改/被何摘要取代
   - 前端可视化：时间轴展示记忆操作历史

3. **摘要层次设计**：
   - L1 日常摘要：每 10 回合生成，压缩日常叙事（importance=0.6）
   - L2 周期摘要：每 50 回合或每章节结束，整合多个 L1（importance=0.8）
   - L3 里程碑摘要：关键事件（突破/死亡/结婚等）触发（importance=0.95）

4. **与现有系统的关系**：
   - 不替代 MemoryCurator.generate_summary_only（继续生成 _history_summaries）
   - 在其基础上增加"写回向量库"和"审计记录"
   - 不替代 NPCReflection（NPC 专属洞察）
   - 关注玩家视角的长期记忆沉淀
"""
from __future__ import annotations
import logging
import re
import threading
import time
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..llm.base_llm import BaseLLM
    from ..db.chroma_db import MemoryStore

logger = logging.getLogger("chronoverse.memory.long_term")


# ──────────────────────────────────────────────────────────────
# 记忆审计日志
# ──────────────────────────────────────────────────────────────


class MemoryAuditLog:
    """[v1.6 P1-6] 记忆操作审计日志：线程安全的环形缓冲。

    记录所有记忆相关操作，支持追溯和前端可视化。
    """

    _instance: "MemoryAuditLog | None" = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._records = deque(maxlen=200)  # 保留最近 200 条
                    inst._enabled = True
                    inst._next_seq = 0
                    cls._instance = inst
        return cls._instance

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, v: bool):
        self._enabled = bool(v)

    def record(self, operation: str, target_id: str = "",
               memory_type: str = "", details: dict | None = None,
               summary: str = "") -> int:
        """记录一次记忆操作。

        参数：
            operation: 操作类型
                - "create" 创建记忆
                - "summarize" 生成摘要
                - "archive" 归档
                - "delete" 删除
                - "modify" 修改
                - "retrieve" 检索（可选记录）
                - "promote" 提升重要性
            target_id: 目标记忆 ID
            memory_type: memory_type 字段（narrative/semantic/episodic 等）
            details: 额外信息
            summary: 摘要文本（如果是 summarize 操作）

        返回：审计记录序号
        """
        if not self._enabled:
            return -1
        with self._lock:
            seq = self._next_seq
            self._next_seq += 1
            entry = {
                "seq": seq,
                "ts": time.time(),
                "operation": operation,
                "target_id": target_id,
                "memory_type": memory_type,
                "details": details or {},
                "summary": summary[:500] if summary else "",
            }
            self._records.append(entry)
        return seq

    def recent(self, limit: int = 50, operation: str = "") -> list[dict]:
        """获取最近 N 条审计记录（最新在前）。"""
        with self._lock:
            items = list(self._records)
        items.reverse()
        if operation:
            items = [r for r in items if r.get("operation") == operation]
        return items[:limit]

    def get_by_target(self, target_id: str) -> list[dict]:
        """获取某条记忆的所有操作历史（按时间顺序）。"""
        if not target_id:
            return []
        with self._lock:
            items = [r for r in self._records if r.get("target_id") == target_id]
        return items

    def stats(self) -> dict:
        """获取审计统计。"""
        with self._lock:
            n = len(self._records)
            ops: dict[str, int] = {}
            for r in self._records:
                op = r.get("operation", "unknown")
                ops[op] = ops.get(op, 0) + 1
        return {
            "total_records": n,
            "max_capacity": self._records.maxlen,
            "enabled": self._enabled,
            "operations": ops,
        }

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._next_seq = 0


# 模块级单例
memory_audit_log = MemoryAuditLog()


# ──────────────────────────────────────────────────────────────
# 长期记忆摘要器
# ──────────────────────────────────────────────────────────────


# L1 日常摘要 prompt
DAILY_SUMMARY_PROMPT = """你是一个游戏世界的记忆官，请将以下游戏叙事片段压缩成一段简洁的日常摘要。

【要求】
1. 保留关键剧情进展、重要 NPC 出场、重大事件
2. 忽略无关紧要的细节、重复的日常描写
3. 字数控制在 300-500 字
4. 用第三人称叙述，保持故事连贯性
5. 如果有玩家的重要选择，一定要提及
6. 开头标注时间范围，如"第X天至第Y天："

【待摘要内容】
{content}

【日常摘要】"""

# L2 周期摘要 prompt（整合多个 L1）
PERIODIC_SUMMARY_PROMPT = """你是一个游戏世界的记忆官，请将以下多个日常摘要整合成一段周期性摘要。

【要求】
1. 提炼这段时间的主线剧情和人物发展弧线
2. 标注重要转折点、人物关系变化
3. 字数控制在 500-800 字
4. 用第三人称叙述，保持故事连贯性
5. 开头标注"【周期摘要】第X天至第Y天"
6. 结尾用一句话总结这段时间的核心主题

【待整合的日常摘要】
{content}

【周期摘要】"""

# L3 里程碑摘要 prompt
MILESTONE_SUMMARY_PROMPT = """你是一个游戏世界的记忆官，玩家刚刚经历了一个重要里程碑事件。
请生成一段里程碑摘要，永久记录这一时刻。

【要求】
1. 详细描述事件的来龙去脉
2. 标注对玩家和世界的长期影响
3. 字数控制在 200-400 字
4. 用第三人称叙述
5. 开头标注"【里程碑】第X天"
6. 结尾用一句话标注此事件的历史意义

【里程碑事件】
事件类型：{event_type}
发生时间：第{day}天
事件描述：{description}

【相关背景】
{context}

【里程碑摘要】"""

# 里程碑事件类型检测
MILESTONE_PATTERNS = {
    "breakthrough": re.compile(r"突破|晋升|进阶|升阶|筑基|金丹|元婴|化神|渡劫|飞升"),
    "death": re.compile(r"死亡|陨落|身亡|逝世|气绝|断气"),
    "marriage": re.compile(r"成婚|结婚|大婚|嫁娶|迎娶"),
    "birth": re.compile(r"出生|诞生|降生|产子|生子"),
    "war": re.compile(r"开战|大战|战役|战争|攻城|决战"),
    "discovery": re.compile(r"发现|获得|传承|遗迹|秘境|宝物|神兵"),
    "betrayal": re.compile(r"背叛|叛变|反水|倒戈"),
    "alliance": re.compile(r"结盟|联盟|合作|联手|投靠"),
}


def detect_milestone(text: str) -> str | None:
    """检测文本中是否包含里程碑事件。返回事件类型或 None。"""
    if not text:
        return None
    for event_type, pattern in MILESTONE_PATTERNS.items():
        if pattern.search(text):
            return event_type
    return None


class LongTermMemorySummarizer:
    """[v1.6 P1-6] 长期记忆 LLM 摘要器。

    生成多层摘要并写回 MemoryStore，使摘要成为可检索的长期记忆。

    摘要层次：
    - L1 日常摘要：每 N 条叙事生成一次（importance=0.6）
    - L2 周期摘要：整合多个 L1（importance=0.8）
    - L3 里程碑摘要：关键事件触发（importance=0.95）

    所有摘要写入 MemoryStore，metadata 标记 type=long_term_summary
    """

    def __init__(self, llm: "BaseLLM | None" = None,
                 memory_store: "MemoryStore | None" = None,
                 audit: "MemoryAuditLog | None" = None):
        self.llm = llm
        self.memory_store = memory_store
        self.audit = audit or memory_audit_log
        # 摘要间隔配置
        self.daily_summary_interval = 10  # 每 10 条叙事生成 L1
        self.periodic_summary_threshold = 5  # 累积 5 个 L1 后生成 L2
        # 内部状态
        self._daily_summaries_since_periodic = 0
        self._last_milestone_day = -1

    def set_llm(self, llm: "BaseLLM"):
        self.llm = llm

    def set_memory_store(self, store: "MemoryStore"):
        self.memory_store = store

    # ── L1 日常摘要 ──────────────────────────────────

    def generate_daily_summary(self, entries: list[dict],
                                current_turn: int = 0,
                                current_day: int = 0) -> dict:
        """生成 L1 日常摘要并写入 MemoryStore。

        参数：
            entries: 待摘要的叙事条目列表
            current_turn: 当前回合
            current_day: 当前天数

        返回：
            {
                "status": "success" | "skipped" | "failed",
                "summary_id": str,
                "summary_text": str,
                "memory_id": str,  # 写入 MemoryStore 的 ID
                "level": "L1",
                "importance": float,
                "entry_count": int,
            }
        """
        if not entries:
            return {"status": "skipped", "reason": "no entries"}

        # 准备内容
        content_parts = []
        for e in entries:
            etype = e.get("type", "narrative")
            text = e.get("text", "")
            if not text:
                continue
            day = e.get("day", "?")
            if etype == "narrative":
                content_parts.append(f"第{day}天：{text[:500]}")
            elif etype == "event":
                content_parts.append(f"【事件】{text[:300]}")
            elif etype == "summary":
                content_parts.append(f"【前期摘要】{text[:300]}")

        if not content_parts:
            return {"status": "skipped", "reason": "no valid content"}

        full_content = "\n".join(content_parts[-20:])  # 最多 20 条
        summary_id = f"ltm_L1_d{current_day}_t{current_turn}"

        # 调用 LLM 生成摘要
        summary_text = self._generate_with_fallback(
            DAILY_SUMMARY_PROMPT.format(content=full_content[:3000]),
            entries,
        )

        if not summary_text:
            return {"status": "failed", "reason": "LLM generation failed"}

        # 写入 MemoryStore
        memory_id = ""
        if self.memory_store:
            try:
                day_start = entries[0].get("day", current_day) if entries else current_day
                day_end = entries[-1].get("day", current_day) if entries else current_day
                memory_id = self.memory_store.add_memory_with_importance(
                    text=summary_text,
                    importance=0.6,
                    memory_type="semantic",
                    metadata={
                        "type": "long_term_summary",
                        "summary_level": "L1",
                        "summary_id": summary_id,
                        "day_range": [day_start, day_end],
                        "turn": current_turn,
                        "day": current_day,
                        "entry_count": len(entries),
                    },
                )
                # 记录审计日志
                self.audit.record(
                    operation="summarize",
                    target_id=memory_id,
                    memory_type="semantic",
                    details={
                        "level": "L1",
                        "summary_id": summary_id,
                        "day_range": [day_start, day_end],
                        "entry_count": len(entries),
                    },
                    summary=summary_text,
                )
                logger.info("L1 daily summary %s created: %d entries, day %d-%d",
                             summary_id, len(entries), day_start, day_end)
            except Exception as e:
                logger.warning("Failed to write L1 summary to MemoryStore: %s", e)
                self.audit.record(
                    operation="summarize",
                    memory_type="semantic",
                    details={"level": "L1", "error": str(e)},
                )

        self._daily_summaries_since_periodic += 1

        return {
            "status": "success",
            "summary_id": summary_id,
            "summary_text": summary_text,
            "memory_id": memory_id,
            "level": "L1",
            "importance": 0.6,
            "entry_count": len(entries),
        }

    # ── L2 周期摘要 ──────────────────────────────────

    def generate_periodic_summary(self, daily_summaries: list[dict],
                                    current_turn: int = 0,
                                    current_day: int = 0) -> dict:
        """生成 L2 周期摘要：整合多个 L1 日常摘要。

        参数：
            daily_summaries: L1 摘要列表（dict 含 text/day_range 字段）
            current_turn: 当前回合
            current_day: 当前天数

        返回：同 generate_daily_summary，level="L2"
        """
        if not daily_summaries or len(daily_summaries) < 2:
            return {"status": "skipped", "reason": "not enough daily summaries"}

        # 准备内容
        content_parts = []
        for s in daily_summaries:
            text = s.get("text", "") if isinstance(s, dict) else str(s)
            if text:
                content_parts.append(text[:500])

        if not content_parts:
            return {"status": "skipped", "reason": "no valid content"}

        full_content = "\n---\n".join(content_parts)
        summary_id = f"ltm_L2_d{current_day}_t{current_turn}"

        summary_text = self._generate_with_fallback(
            PERIODIC_SUMMARY_PROMPT.format(content=full_content[:4000]),
            daily_summaries,
        )

        if not summary_text:
            return {"status": "failed", "reason": "LLM generation failed"}

        # 写入 MemoryStore
        memory_id = ""
        if self.memory_store:
            try:
                # 计算时间范围
                all_days = []
                for s in daily_summaries:
                    dr = s.get("day_range", []) if isinstance(s, dict) else []
                    all_days.extend(dr)
                day_start = min(all_days) if all_days else current_day
                day_end = max(all_days) if all_days else current_day

                memory_id = self.memory_store.add_memory_with_importance(
                    text=summary_text,
                    importance=0.8,
                    memory_type="semantic",
                    metadata={
                        "type": "long_term_summary",
                        "summary_level": "L2",
                        "summary_id": summary_id,
                        "day_range": [day_start, day_end],
                        "turn": current_turn,
                        "day": current_day,
                        "entry_count": len(daily_summaries),
                        "source_summaries": [s.get("summary_id", "") for s in daily_summaries if isinstance(s, dict)],
                    },
                )
                self.audit.record(
                    operation="summarize",
                    target_id=memory_id,
                    memory_type="semantic",
                    details={
                        "level": "L2",
                        "summary_id": summary_id,
                        "day_range": [day_start, day_end],
                        "source_count": len(daily_summaries),
                    },
                    summary=summary_text,
                )
                logger.info("L2 periodic summary %s created: %d L1 summaries, day %d-%d",
                             summary_id, len(daily_summaries), day_start, day_end)
            except Exception as e:
                logger.warning("Failed to write L2 summary to MemoryStore: %s", e)

        self._daily_summaries_since_periodic = 0  # 重置计数

        return {
            "status": "success",
            "summary_id": summary_id,
            "summary_text": summary_text,
            "memory_id": memory_id,
            "level": "L2",
            "importance": 0.8,
            "entry_count": len(daily_summaries),
        }

    # ── L3 里程碑摘要 ──────────────────────────────────

    def generate_milestone_summary(self, event_type: str,
                                    description: str, day: int,
                                    context: str = "",
                                    turn: int = 0) -> dict:
        """生成 L3 里程碑摘要。

        参数：
            event_type: 里程碑类型（breakthrough/death/marriage 等）
            description: 事件描述
            day: 发生天数
            context: 相关背景（可选）
            turn: 当前回合

        返回：同上，level="L3"
        """
        if not description:
            return {"status": "skipped", "reason": "no description"}

        summary_id = f"ltm_L3_{event_type}_d{day}"

        summary_text = self._generate_with_fallback(
            MILESTONE_SUMMARY_PROMPT.format(
                event_type=event_type,
                day=day,
                description=description[:1000],
                context=(context or "（无）")[:500],
            ),
            [{"text": description}],
        )

        if not summary_text:
            return {"status": "failed", "reason": "LLM generation failed"}

        # 写入 MemoryStore
        memory_id = ""
        if self.memory_store:
            try:
                memory_id = self.memory_store.add_memory_with_importance(
                    text=summary_text,
                    importance=0.95,
                    memory_type="semantic",
                    metadata={
                        "type": "long_term_summary",
                        "summary_level": "L3",
                        "summary_id": summary_id,
                        "milestone_type": event_type,
                        "day": day,
                        "turn": turn,
                    },
                )
                self.audit.record(
                    operation="summarize",
                    target_id=memory_id,
                    memory_type="semantic",
                    details={
                        "level": "L3",
                        "summary_id": summary_id,
                        "milestone_type": event_type,
                        "day": day,
                    },
                    summary=summary_text,
                )
                logger.info("L3 milestone summary %s created: %s on day %d",
                             summary_id, event_type, day)
            except Exception as e:
                logger.warning("Failed to write L3 summary to MemoryStore: %s", e)

        self._last_milestone_day = day

        return {
            "status": "success",
            "summary_id": summary_id,
            "summary_text": summary_text,
            "memory_id": memory_id,
            "level": "L3",
            "importance": 0.95,
            "milestone_type": event_type,
        }

    def check_and_generate_milestone(self, narrative: str, day: int,
                                      context: str = "", turn: int = 0) -> dict | None:
        """检查叙事中是否包含里程碑事件，如果是则生成 L3 摘要。

        返回：如果生成了里程碑摘要则返回结果 dict，否则返回 None。
        """
        if not narrative or day <= self._last_milestone_day:
            return None
        event_type = detect_milestone(narrative)
        if not event_type:
            return None
        return self.generate_milestone_summary(
            event_type=event_type,
            description=narrative[:1000],
            day=day,
            context=context,
            turn=turn,
        )

    # ── 查询接口 ──────────────────────────────────

    def get_long_term_summaries(self, level: str = "",
                                 limit: int = 20) -> list[dict]:
        """从 MemoryStore 检索长期记忆摘要。

        参数：
            level: 过滤摘要级别（L1/L2/L3），空字符串表示全部
            limit: 返回数量上限
        """
        if not self.memory_store:
            return []
        try:
            # [Bug] chromadb 多字段 where 在某些版本下匹配异常，
            # 改为只按 type 过滤，level 在客户端二次过滤
            results = self.memory_store.collection.get(
                where={"type": "long_term_summary"},
                include=["metadatas", "documents"],
            )
            # 转换为统一格式
            summaries = []
            ids = results.get("ids", [])
            docs = results.get("documents", [])
            metas = results.get("metadatas", [])
            for i, mid in enumerate(ids):
                doc = docs[i] if i < len(docs) else ""
                meta = metas[i] if i < len(metas) else {}
                lv = meta.get("summary_level", "?")
                # 客户端级别过滤
                if level and lv != level:
                    continue
                summaries.append({
                    "id": mid,
                    "text": doc[:500],
                    "level": lv,
                    "summary_id": meta.get("summary_id", ""),
                    "day_range": meta.get("day_range", []),
                    "day": meta.get("day", 0),
                    "turn": meta.get("turn", 0),
                    "importance": meta.get("importance", 0),
                    "milestone_type": meta.get("milestone_type", ""),
                    "entry_count": meta.get("entry_count", 0),
                })
            # 按天倒序
            summaries.sort(key=lambda x: x.get("day", 0), reverse=True)
            return summaries[:limit]
        except Exception as e:
            logger.warning("Failed to query long-term summaries: %s", e)
            return []

    def fetch_milestones_for_retrieval(self, query: str,
                                        max_results: int = 3) -> list[dict]:
        """[v1.6 P1-7] 里程碑强制召回：根据查询文本触发相关的 L3 摘要召回。

        当叙事/查询包含"突破/死亡/结婚"等关键词时，应从 L3 摘要中
        强制召回相关里程碑，避免检索系统因关键词覆盖不足而漏掉关键剧情。

        参数：
            query: 查询文本（通常是玩家输入或叙事片段）
            max_results: 最大返回数量

        返回：[{id, text, score, source, summary_level, milestone_type, day}]
        """
        if not self.memory_store or not query:
            return []
        try:
            # 1. 从查询文本检测里程碑事件类型
            event_types: set[str] = set()
            for et, pattern in MILESTONE_PATTERNS.items():
                if pattern.search(query):
                    event_types.add(et)
            if not event_types:
                return []

            # 2. 拉取所有 L3 摘要（客户端过滤）
            results = self.memory_store.collection.get(
                where={"type": "long_term_summary"},
                include=["metadatas", "documents"],
            )
            milestones: list[dict] = []
            ids = results.get("ids", [])
            docs = results.get("documents", [])
            metas = results.get("metadatas", [])
            for i, mid in enumerate(ids):
                meta = metas[i] if i < len(metas) else {}
                if meta.get("summary_level") != "L3":
                    continue
                m_type = meta.get("milestone_type", "")
                # 3. 匹配：事件类型命中查询中检测到的类型
                if m_type and m_type not in event_types:
                    continue
                doc = docs[i] if i < len(docs) else ""
                milestones.append({
                    "id": mid,
                    "text": doc,
                    "score": 0.95,  # 里程碑强制召回给予高分
                    "source": "milestone_recall",
                    "summary_level": "L3",
                    "milestone_type": m_type,
                    "day": meta.get("day", 0),
                    "is_long_term_summary": True,
                    "forced_recall": True,
                })
            # 按天倒序，限制数量
            milestones.sort(key=lambda x: x.get("day", 0), reverse=True)
            return milestones[:max_results]
        except Exception as e:
            logger.warning("Failed to fetch milestones for retrieval: %s", e)
            return []

    # ── 内部工具 ──────────────────────────────────

    def _generate_with_fallback(self, prompt: str, entries: list) -> str:
        """调用 LLM 生成摘要，失败时回退到规则提取。"""
        if not self.llm:
            return self._rule_based_summary(entries)
        try:
            result = self.llm.chat(prompt, temperature=0.4, max_tokens=1024)
            text = (result or "").strip()
            if text and len(text) > 20:
                return text
            return self._rule_based_summary(entries)
        except Exception as e:
            logger.warning("LLM summary generation failed, using rule-based: %s", e)
            return self._rule_based_summary(entries)

    @staticmethod
    def _rule_based_summary(entries: list) -> str:
        """规则回退：提取关键句和实体。"""
        if not entries:
            return ""
        days = set()
        events = []
        npcs = set()
        for e in entries:
            if isinstance(e, dict):
                if e.get("day"):
                    days.add(e["day"])
                text = e.get("text", "")
                if e.get("type") == "event" or len(events) < 5:
                    events.append(text[:100])
                # 简单人名提取
                for m in re.finditer(r"[\u4e00-\u9fff]{2,4}(?:道|说|笑|怒|惊)", text):
                    npcs.add(m.group(0)[:-1])
        day_str = f"第{min(days)}天至第{max(days)}天" if days else "近期"
        event_str = "；".join(events[:3]) if events else "发生了一些故事"
        npc_str = "、".join(list(npcs)[:5]) if npcs else "若干人物"
        return f"{day_str}，{npc_str}等人的故事。主要事件：{event_str}。（规则回退摘要）"
