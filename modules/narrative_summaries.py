"""
[2026-08-10] 剧情概况分层记忆（用户方案）

架构：
- 每轮叙事生成后，用 cheap_llm 生成 ~300 字剧情概况，存入 history.db（narrative_summaries 表）
- 概况满 CHUNK_SIZE（100）条 → 用主 LLM 压缩成 ~2 万字剧情纪要（narrative_chunks 表），删除已压缩概况
- 上下文组装（普通叙事 + 章节生成共用）：
    头部 = chunk 纪要序列（旧→新，超预算时裁掉最旧）+ 当前批逐轮概况
    尾部 = 最近 7 轮叙事原文 + 世界设定 + NPC 档案
  预算 15 万 token（1M 上下文模型）；头部稳定内容前置以命中 API 上下文缓存
"""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger("chronoverse.narrative_summaries")

CHUNK_SIZE = 100        # 每 100 条概况压缩为一个 chunk
SUMMARY_TARGET = 300    # 每条概况目标字数
CHUNK_TARGET = 20000    # chunk 目标字数（2 万字）


class NarrativeSummaryStore:
    """概况/chunk 的存取（history.db 两张表）+ 头部文本组装。"""

    def __init__(self, world_id: str, base_dir: Path):
        self.world_id = world_id
        # base_dir 为 saves 根目录，库文件在 <base_dir>/<world_id>/history.db
        self.db_path = Path(base_dir) / world_id / "history.db"
        self._init_tables()

    # ── 基础 ──
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self):
        try:
            with self._connect() as conn:
                # [2026-08-10] 兼容迁移：旧版 narrative_chunks 用 start_turn/end_turn 列，
                # 新版用 start_id/end_id（id 是权威顺序）。旧表若为空直接改名搁置（不删数据）。
                try:
                    cols = [c[1] for c in conn.execute("PRAGMA table_info(narrative_chunks)").fetchall()]
                    if cols and "start_turn" in cols:
                        conn.execute("ALTER TABLE narrative_chunks RENAME TO narrative_chunks_legacy")
                        logger.info("旧版 narrative_chunks 已改名 narrative_chunks_legacy（空表搁置）")
                except Exception as e:
                    logger.warning("narrative_chunks 迁移检查失败: %s", e)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS narrative_summaries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        world_id TEXT NOT NULL,
                        turn INTEGER NOT NULL,
                        day INTEGER NOT NULL,
                        summary TEXT NOT NULL,
                        timestamp TEXT NOT NULL DEFAULT (datetime('now'))
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS narrative_chunks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        world_id TEXT NOT NULL,
                        start_id INTEGER NOT NULL,
                        end_id INTEGER NOT NULL,
                        chunk_text TEXT NOT NULL,
                        timestamp TEXT NOT NULL DEFAULT (datetime('now'))
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_sums_world_turn ON narrative_summaries(world_id, turn)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_world ON narrative_chunks(world_id, start_id)")
        except Exception as e:
            logger.warning("narrative_summaries 建表失败: %s", e)

    # ── 写入 ──
    def add_summary(self, turn: int, day: int, summary: str):
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO narrative_summaries(world_id, turn, day, summary) VALUES(?,?,?,?)",
                    (self.world_id, turn, day, summary),
                )
        except Exception as e:
            logger.warning("add_summary 失败: %s", e)

    def add_chunk(self, start_id: int, end_id: int, chunk_text: str):
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO narrative_chunks(world_id, start_id, end_id, chunk_text) VALUES(?,?,?,?)",
                    (self.world_id, start_id, end_id, chunk_text),
                )
        except Exception as e:
            logger.warning("add_chunk 失败: %s", e)

    def delete_summaries_upto(self, end_id: int):
        """删除已压缩进 chunk 的概况（id <= end_id）。"""
        try:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM narrative_summaries WHERE world_id=? AND id<=?",
                    (self.world_id, end_id),
                )
        except Exception as e:
            logger.warning("delete_summaries_upto 失败: %s", e)

    def delete_after_turn(self, target_turn: int) -> int:
        """[2026-08-10] 撤销/重试回滚：删除 turn > target_turn 的剧情概况。

        若被删概况已落入某个 chunk 的覆盖区间（极端情况：撤销跨过压缩点），
        连带删除该 chunk，避免被撤销的剧情残留在头部纪要中。
        返回删除的概况条数。
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id FROM narrative_summaries WHERE world_id=? AND turn>?",
                    (self.world_id, target_turn),
                ).fetchall()
                del_ids = [r[0] for r in rows]
                if not del_ids:
                    return 0
                conn.execute(
                    "DELETE FROM narrative_summaries WHERE world_id=? AND turn>?",
                    (self.world_id, target_turn),
                )
                # 若被删概况已压缩进 chunk（start_id<=max 且 end_id>=min），删除该 chunk
                min_id, max_id = min(del_ids), max(del_ids)
                n_chunks = conn.execute(
                    "DELETE FROM narrative_chunks WHERE world_id=? AND start_id<=? AND end_id>=?",
                    (self.world_id, max_id, min_id),
                ).rowcount
                if n_chunks:
                    logger.info("delete_after_turn: 连带删除 %d 个覆盖被撤销区间的 chunk", n_chunks)
                return len(del_ids)
        except Exception as e:
            logger.warning("delete_after_turn 失败: %s", e)
            return 0


    # ── 读取 ──
    def get_summaries(self) -> list[dict]:
        """按 id 升序（id 是权威顺序；turn 字段仅展示，历史数据可能乱序）。"""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id, turn, day, summary FROM narrative_summaries WHERE world_id=? ORDER BY id ASC",
                    (self.world_id,),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception:
            return []

    def get_chunks(self) -> list[dict]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id, start_id, end_id, chunk_text FROM narrative_chunks WHERE world_id=? ORDER BY id ASC",
                    (self.world_id,),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception:
            return []

    def last_chunk_end_id(self) -> int:
        chunks = self.get_chunks()
        return chunks[-1]["end_id"] if chunks else 0

    def uncompressed_summaries(self) -> list[dict]:
        """尚未进入 chunk 的概况（id 大于最后一个 chunk 的 end_id）。"""
        last_end = self.last_chunk_end_id()
        return [s for s in self.get_summaries() if s["id"] > last_end]

    # ── 头部文本组装（普通叙事 + 章节生成共用）──
    def build_head_text(self, max_chars: int = 70000) -> str:
        """组装头部：chunk 纪要（旧→新，超预算裁最旧）+ 当前批逐轮概况。

        max_chars: 头部预算（中文字数，默认 70000 ≈ 10.5 万 token）。
        返回空串表示暂无概况/chunk。
        """
        chunks = self.get_chunks()
        sums = self.get_summaries()

        sums_text = "\n".join(
            f"[第{row['day']}天 · 轮{row['turn']}] {row['summary']}" for row in sums
        )
        budget = max_chars - len(sums_text)

        # chunk 从新到旧保留，直到预算耗尽（最旧的被裁掉）
        kept = []
        for ch in reversed(chunks):
            if budget - len(ch["chunk_text"]) < 0:
                break
            kept.append(ch)
            budget -= len(ch["chunk_text"])
        kept.reverse()  # 恢复旧→新顺序

        parts = []
        for ch in kept:
            parts.append(f"【剧情纪要 · 第{ch['start_id']}~{ch['end_id']}条】\n{ch['chunk_text']}")
        if sums:
            parts.append("【近期剧情概况（逐轮，旧→新）】\n" + sums_text)
        return "\n\n".join(parts)
