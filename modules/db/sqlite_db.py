from __future__ import annotations
import sqlite3
import json
import threading
import uuid
from datetime import datetime
from pathlib import Path


class WorldDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # [Bug H5] 多线程访问 SQLite 需要锁保护所有数据库操作
        self._lock = threading.Lock()
        self._init_tables()

    def _init_tables(self):
        with self._lock:
            cur = self.conn.cursor()
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    day INTEGER NOT NULL,
                    time TEXT,
                    event_id TEXT UNIQUE,
                    event_type TEXT,
                    description TEXT,
                    affected_locations TEXT,
                    affected_agents TEXT,
                    impact_level INTEGER DEFAULT 5,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    turn INTEGER NOT NULL,
                    day INTEGER,
                    agent_id TEXT NOT NULL,
                    agent_name TEXT,
                    action_type TEXT,
                    detail TEXT,
                    location TEXT,
                    target_agent TEXT,
                    result TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS relation_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    day INTEGER,
                    agent_a TEXT NOT NULL,
                    agent_b TEXT NOT NULL,
                    change TEXT,
                    reason TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS economy_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    day INTEGER,
                    item_name TEXT,
                    old_price REAL,
                    new_price REAL,
                    reason TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """)
            self.conn.commit()

    def log_event(self, event: dict):
        # [Bug H6] 空 event_id 会导致 INSERT OR REPLACE 互相覆盖，生成唯一 ID
        event_id = event.get("event_id") or f"evt_{uuid.uuid4().hex[:12]}"
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO events
                (day, time, event_id, event_type, description,
                 affected_locations, affected_agents, impact_level)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event.get("day", 0),
                event.get("time", ""),
                event_id,
                event.get("event_type", ""),
                event.get("description", ""),
                json.dumps(event.get("affected_locations", []), ensure_ascii=False),
                json.dumps(event.get("affected_agents", []), ensure_ascii=False),
                event.get("impact_level", 5),
            ))
            self.conn.commit()

    def log_action(self, action: dict):
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("""
                INSERT INTO actions
                (turn, day, agent_id, agent_name, action_type, detail,
                 location, target_agent, result)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                action.get("turn", 0),
                action.get("day", 0),
                action.get("agent_id", ""),
                action.get("agent_name", ""),
                action.get("action_type", ""),
                action.get("detail", ""),
                action.get("location", ""),
                action.get("target_agent", ""),
                action.get("result", ""),
            ))
            self.conn.commit()

    def log_relation_change(self, agent_a: str, agent_b: str,
                            change: str, reason: str, day: int = 0):
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("""
                INSERT INTO relation_log (day, agent_a, agent_b, change, reason)
                VALUES (?, ?, ?, ?, ?)
            """, (day, agent_a, agent_b, change, reason))
            self.conn.commit()

    def log_economy(self, day: int, item_name: str,
                    old_price: float, new_price: float, reason: str = ""):
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("""
                INSERT INTO economy_log (day, item_name, old_price, new_price, reason)
                VALUES (?, ?, ?, ?, ?)
            """, (day, item_name, old_price, new_price, reason))
            self.conn.commit()

    def get_events_today(self, day: int) -> list[dict]:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("SELECT * FROM events WHERE day = ? ORDER BY time", (day,))
            return [dict(row) for row in cur.fetchall()]

    def get_recent_actions(self, n: int = 10) -> list[dict]:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("SELECT * FROM actions ORDER BY id DESC LIMIT ?", (n,))
            return [dict(row) for row in reversed(cur.fetchall())]

    def get_full_history(self) -> list[dict]:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("SELECT * FROM actions ORDER BY id ASC")
            return [dict(row) for row in cur.fetchall()]

    def get_all_events(self) -> list[dict]:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("SELECT * FROM events ORDER BY day, time")
            return [dict(row) for row in cur.fetchall()]

    def get_stats(self) -> dict:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("SELECT COUNT(*) FROM events")
            event_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM actions")
            action_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM relation_log")
            relation_count = cur.fetchone()[0]
            return {
                "events": event_count,
                "actions": action_count,
                "relation_changes": relation_count,
            }

    def close(self):
        with self._lock:
            self.conn.close()
