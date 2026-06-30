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
                diff_summary TEXT
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
                metadata TEXT
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

        conn.commit()
        conn.close()

    def save_snapshot(self, world_id: str, turn: int, day: int, time: str,
                     player_state, world_state, npc_states: dict,
                     narrative: str = "", player_input: str = "",
                     diff_summary: str = ""):
        """保存一个状态快照"""
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
             npc_states, narrative_text, player_input, diff_summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            world_id, turn, day, time,
            datetime.now().isoformat(),
            json.dumps(player_dict, ensure_ascii=False),
            json.dumps(world_dict, ensure_ascii=False),
            json.dumps(npc_dict, ensure_ascii=False),
            narrative, player_input, diff_summary,
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
        """将数据库行转换为StateSnapshot"""
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
        )
