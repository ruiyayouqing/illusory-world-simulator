"""
[v9] 持久化快照与历史回溯 — 世界状态的版本管理

设计原则：
  - 每个turn保存世界状态快照（或diff）
  - 支持回放——从任意时间点重放世界变化
  - 可以导出世界演化报告
"""
from __future__ import annotations
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("chronoverse.state_history")


@dataclass
class StateSnapshot:
    """世界状态快照"""
    snapshot_id: int = 0
    world_id: str = ""
    turn: int = 0
    day: int = 1
    time: str = "清晨"
    timestamp: str = ""
    player_state: dict = field(default_factory=dict)
    world_state: dict = field(default_factory=dict)
    npc_states: dict = field(default_factory=dict)
    narrative_text: str = ""
    player_input: str = ""
    diff_summary: str = ""  # 与上一个快照的差异摘要
    # [v12] 分支管理字段
    branch_id: str = "main"               # 分支ID（主线/平行世界）
    parent_snapshot_id: int | None = None  # 父快照（分支起点）
    divergence_point: bool = False         # 是否是分歧点
    is_active: bool = True                 # 是否属于当前活跃分支


class StateHistoryManager:
    """世界状态历史管理器"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS state_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                world_id TEXT NOT NULL,
                turn INTEGER NOT NULL,
                day INTEGER NOT NULL,
                time TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                player_state TEXT,
                world_state TEXT,
                npc_states TEXT,
                narrative_text TEXT,
                player_input TEXT,
                diff_summary TEXT,
                branch_id TEXT DEFAULT 'main',
                parent_snapshot_id INTEGER,
                divergence_point INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS narrative_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                world_id TEXT NOT NULL,
                turn INTEGER NOT NULL,
                day INTEGER NOT NULL,
                time TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                entry_type TEXT NOT NULL,
                player_input TEXT,
                narrative TEXT,
                image_url TEXT,
                options TEXT,
                metadata TEXT,
                branch_id TEXT DEFAULT 'main'
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_snapshots_world_turn
            ON state_snapshots(world_id, turn)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_narrative_world_turn
            ON narrative_history(world_id, turn)
        """)

        # [v12] 分支索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_snapshots_branch
            ON state_snapshots(world_id, branch_id, turn)
        """)

        conn.commit()
        conn.close()

    def save_snapshot(self, world_id: str, turn: int, day: int, time: str,
                     player_state, world_state, npc_states: dict,
                     narrative: str = "", player_input: str = "",
                     diff_summary: str = "",
                     branch_id: str = "main",
                     parent_snapshot_id: int = None,
                     divergence_point: bool = False):
        """保存一个状态快照（[v12] 支持分支）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        player_dict = player_state.model_dump() if hasattr(player_state, 'model_dump') else {}
        world_dict = world_state.model_dump() if hasattr(world_state, 'model_dump') else {}
        npc_dict = {}
        if npc_states:
            for nid, npc in npc_states.items():
                npc_dict[nid] = npc.model_dump() if hasattr(npc, 'model_dump') else {}

        cursor.execute("""
            INSERT INTO state_snapshots
            (world_id, turn, day, time, timestamp, player_state, world_state,
             npc_states, narrative_text, player_input, diff_summary,
             branch_id, parent_snapshot_id, divergence_point, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            world_id, turn, day, time,
            datetime.now().isoformat(),
            json.dumps(player_dict, ensure_ascii=False),
            json.dumps(world_dict, ensure_ascii=False),
            json.dumps(npc_dict, ensure_ascii=False),
            narrative, player_input, diff_summary,
            branch_id, parent_snapshot_id,
            1 if divergence_point else 0, 1,
        ))

        conn.commit()
        conn.close()

    def save_narrative_entry(self, world_id: str, turn: int, day: int, time: str,
                            entry_type: str, player_input: str = "",
                            narrative: str = "", image_url: str = "",
                            options: list = None, metadata: dict = None):
        """保存一条叙事记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO narrative_history
            (world_id, turn, day, time, timestamp, entry_type,
             player_input, narrative, image_url, options, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            world_id, turn, day, time,
            datetime.now().isoformat(),
            entry_type,
            player_input, narrative, image_url,
            json.dumps(options or [], ensure_ascii=False),
            json.dumps(metadata or {}, ensure_ascii=False),
        ))

        conn.commit()
        conn.close()

    def get_snapshot(self, world_id: str, turn: int) -> Optional[StateSnapshot]:
        """获取指定回合的快照"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM state_snapshots
            WHERE world_id = ? AND turn = ?
        """, (world_id, turn))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return self._row_to_snapshot(row)

    def get_latest_snapshot(self, world_id: str) -> Optional[StateSnapshot]:
        """获取最新快照"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM state_snapshots
            WHERE world_id = ?
            ORDER BY turn DESC LIMIT 1
        """, (world_id,))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return self._row_to_snapshot(row)

    def get_snapshot_range(self, world_id: str, start_turn: int = 0,
                          end_turn: int = 999999) -> list[StateSnapshot]:
        """获取某个范围内的所有快照"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM state_snapshots
            WHERE world_id = ? AND turn >= ? AND turn <= ?
            ORDER BY turn ASC
        """, (world_id, start_turn, end_turn))

        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_snapshot(row) for row in rows]

    def get_narrative_history(self, world_id: str, start_turn: int = 0,
                             end_turn: int = 999999,
                             entry_type: str = None) -> list[dict]:
        """获取叙事历史"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        if entry_type:
            cursor.execute("""
                SELECT * FROM narrative_history
                WHERE world_id = ? AND turn >= ? AND turn <= ? AND entry_type = ?
                ORDER BY turn ASC
            """, (world_id, start_turn, end_turn, entry_type))
        else:
            cursor.execute("""
                SELECT * FROM narrative_history
                WHERE world_id = ? AND turn >= ? AND turn <= ?
                ORDER BY turn ASC
            """, (world_id, start_turn, end_turn))

        rows = cursor.fetchall()
        conn.close()

        results = []
        for row in rows:
            results.append({
                "id": row[0],
                "world_id": row[1],
                "turn": row[2],
                "day": row[3],
                "time": row[4],
                "timestamp": row[5],
                "entry_type": row[6],
                "player_input": row[7] or "",
                "narrative": row[8] or "",
                "image_url": row[9] or "",
                "options": json.loads(row[10]) if row[10] else [],
                "metadata": json.loads(row[11]) if row[11] else {},
            })

        return results

    def get_full_narrative(self, world_id: str) -> str:
        """获取完整叙事文本（用于导出）"""
        entries = self.get_narrative_history(world_id)
        parts = []
        for entry in entries:
            if entry["entry_type"] == "player_input":
                parts.append(f"\n【第{entry['day']}天 {entry['time']}】")
                parts.append(f"你：{entry['player_input']}")
            elif entry["entry_type"] == "narrative":
                parts.append(entry["narrative"])
            elif entry["entry_type"] == "event":
                parts.append(f"[事件] {entry['narrative']}")
            if entry.get("image_url"):
                parts.append(f"[图片] {entry['image_url']}")
        return "\n\n".join(parts)

    # [v1.4 P1-7] 统一 narrative 持久化：新增 search/stats 方法，
    # 让搜索/统计接口可从 HistoryDB 读取，取代 MetaDB.narrative 冗余表

    def search_narrative(self, world_id: str, keyword: str, limit: int = 20) -> list[dict]:
        """[v1.4] 关键词搜索叙事历史（替代 MetaDB.narrative.search_narrative）"""
        # 转义 LIKE 通配符
        escaped = keyword.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT entry_type, day, time, narrative, player_input FROM narrative_history "
            "WHERE world_id=? AND narrative LIKE ? ESCAPE '\\' "
            "ORDER BY day DESC LIMIT ?",
            (world_id, f"%{escaped}%", limit)
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                "type": r[0], "day": r[1], "time": r[2],
                "text": r[3] or "", "player_input": r[4] or "",
            }
            for r in rows
        ]

    def get_stats(self, world_id: str) -> dict:
        """[v1.4] 叙事统计（替代 MetaDB.narrative.get_stats）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM narrative_history WHERE world_id=?", (world_id,)
        )
        total = cursor.fetchone()[0]
        cursor.execute(
            "SELECT MAX(day) FROM narrative_history WHERE world_id=?", (world_id,)
        )
        max_day = cursor.fetchone()[0] or 0
        cursor.execute(
            "SELECT COUNT(*) FROM narrative_history WHERE world_id=? AND entry_type='narrative'",
            (world_id,)
        )
        narratives = cursor.fetchone()[0]
        conn.close()
        return {"total_entries": total, "max_day": max_day, "narrative_count": narratives}

    def compute_diff(self, old_snapshot: StateSnapshot,
                    new_snapshot: StateSnapshot) -> str:
        """计算两个快照之间的差异"""
        diffs = []

        # 玩家状态差异
        old_player = old_snapshot.player_state
        new_player = new_snapshot.player_state

        if old_player.get("stats", {}).get("health") != new_player.get("stats", {}).get("health"):
            old_h = old_player.get("stats", {}).get("health", 0)
            new_h = new_player.get("stats", {}).get("health", 0)
            diffs.append(f"生命: {old_h} -> {new_h}")

        if old_player.get("stats", {}).get("energy") != new_player.get("stats", {}).get("energy"):
            old_e = old_player.get("stats", {}).get("energy", 0)
            new_e = new_player.get("stats", {}).get("energy", 0)
            diffs.append(f"体力: {old_e} -> {new_e}")

        if old_player.get("social", {}).get("reputation") != new_player.get("social", {}).get("reputation"):
            old_r = old_player.get("social", {}).get("reputation", 0)
            new_r = new_player.get("social", {}).get("reputation", 0)
            diffs.append(f"声望: {old_r} -> {new_r}")

        # 世界状态差异
        old_world = old_snapshot.world_state
        new_world = new_snapshot.world_state

        if old_world.get("crisis_level") != new_world.get("crisis_level"):
            old_c = old_world.get("crisis_level", 0)
            new_c = new_world.get("crisis_level", 0)
            diffs.append(f"危机等级: {old_c} -> {new_c}")

        if old_world.get("current_day") != new_world.get("current_day"):
            old_d = old_world.get("current_day", 0)
            new_d = new_world.get("current_day", 0)
            diffs.append(f"天数: {old_d} -> {new_d}")

        return "；".join(diffs) if diffs else "无明显变化"

    def generate_world_report(self, world_id: str) -> str:
        """生成世界演化报告"""
        snapshots = self.get_snapshot_range(world_id)
        if not snapshots:
            return "暂无历史数据"

        first = snapshots[0]
        last = snapshots[-1]

        report = [
            f"# 世界演化报告",
            f"",
            f"## 基本信息",
            f"- 世界ID: {world_id}",
            f"- 总回合数: {len(snapshots)}",
            f"- 起始天数: 第{first.day}天",
            f"- 当前天数: 第{last.day}天",
            f"",
            f"## 状态变化",
        ]

        # 玩家变化
        old_p = first.player_state
        new_p = last.player_state
        if old_p and new_p:
            report.append(f"### 玩家")
            for stat in ["health", "energy", "strength", "agility", "intelligence"]:
                old_val = old_p.get("stats", {}).get(stat, "?")
                new_val = new_p.get("stats", {}).get(stat, "?")
                if old_val != new_val:
                    report.append(f"- {stat}: {old_val} → {new_val}")

        # 世界变化
        old_w = first.world_state
        new_w = last.world_state
        if old_w and new_w:
            report.append(f"### 世界")
            report.append(f"- 危机等级: {old_w.get('crisis_level', 0)} → {new_w.get('crisis_level', 0)}")
            report.append(f"- 天气: {old_w.get('weather', '?')} → {new_w.get('weather', '?')}")

        return "\n".join(report)

    def _row_to_snapshot(self, row) -> StateSnapshot:
        """将数据库行转换为StateSnapshot（[v12] 含分支字段）"""
        # 向后兼容：旧数据可能没有分支字段
        branch_id = row[12] if len(row) > 12 and row[12] else "main"
        parent_id = row[13] if len(row) > 13 else None
        div_point = bool(row[14]) if len(row) > 14 else False
        is_active = bool(row[15]) if len(row) > 15 else True

        return StateSnapshot(
            snapshot_id=row[0],
            world_id=row[1],
            turn=row[2],
            day=row[3],
            time=row[4],
            timestamp=row[5],
            player_state=json.loads(row[6]) if row[6] else {},
            world_state=json.loads(row[7]) if row[7] else {},
            npc_states=json.loads(row[8]) if row[8] else {},
            narrative_text=row[9] or "",
            player_input=row[10] or "",
            diff_summary=row[11] or "",
            branch_id=branch_id,
            parent_snapshot_id=parent_id,
            divergence_point=div_point,
            is_active=is_active,
        )

    # ── [v12] 分支管理（Letta式Git版本控制） ────────────────

    def create_branch(self, world_id: str, from_snapshot_id: int,
                      branch_name: str) -> str:
        """
        [v12] 从某个快照创建新分支（平行世界）。
        玩家选不同时间轴进入 = 创建新分支。

        返回：新分支ID
        """
        branch_id = f"branch_{branch_name}_{int(datetime.now().timestamp())}"

        # 获取父快照
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM state_snapshots WHERE id = ?",
            (from_snapshot_id,)
        )
        parent_row = cursor.fetchone()
        conn.close()

        if not parent_row:
            logger.warning("create_branch: 父快照 %d 不存在", from_snapshot_id)
            return "main"

        # 标记父快照为分歧点
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE state_snapshots SET divergence_point = 1 WHERE id = ?",
            (from_snapshot_id,)
        )
        conn.commit()
        conn.close()

        logger.info("创建分支: %s (从快照 %d)", branch_id, from_snapshot_id)
        return branch_id

    def get_active_branch(self, world_id: str) -> str:
        """[v12] 获取当前活跃分支"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT branch_id FROM state_snapshots
            WHERE world_id = ? AND is_active = 1
            ORDER BY turn DESC LIMIT 1
        """, (world_id,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else "main"

    def get_branches(self, world_id: str) -> list[dict]:
        """[v12] 获取所有分支列表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT branch_id, MIN(turn) as start_turn,
                   MAX(turn) as end_turn, COUNT(*) as snapshot_count
            FROM state_snapshots
            WHERE world_id = ?
            GROUP BY branch_id
            ORDER BY start_turn
        """, (world_id,))
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                "branch_id": r[0],
                "start_turn": r[1],
                "end_turn": r[2],
                "snapshot_count": r[3],
            }
            for r in rows
        ]

    def rollback_to(self, world_id: str, snapshot_id: int):
        """
        [v12] 回滚到某个快照（Letta式Git回滚）。
        该快照之后的所有快照标记为 inactive。
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 获取目标快照的 turn
        cursor.execute(
            "SELECT turn FROM state_snapshots WHERE id = ?",
            (snapshot_id,)
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return

        target_turn = row[0]

        # 标记之后的快照为 inactive
        cursor.execute("""
            UPDATE state_snapshots
            SET is_active = 0
            WHERE world_id = ? AND turn > ?
        """, (world_id, target_turn))

        conn.commit()
        conn.close()
        logger.info("回滚: %s 到快照 %d (turn %d)",
                    world_id, snapshot_id, target_turn)

    def get_divergence_points(self, world_id: str) -> list[dict]:
        """[v12] 获取所有分歧点（可供玩家选择的时间节点）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, turn, day, narrative_text, branch_id
            FROM state_snapshots
            WHERE world_id = ? AND divergence_point = 1
            ORDER BY turn ASC
        """, (world_id,))
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                "snapshot_id": r[0],
                "turn": r[1],
                "day": r[2],
                "narrative": r[3] or "",
                "branch_id": r[4],
            }
            for r in rows
        ]
