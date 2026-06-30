from __future__ import annotations
import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger("chronoverse.db")


class WorldMetaDB:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self):
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS worlds (
                world_id TEXT PRIMARY KEY,
                world_name TEXT NOT NULL,
                world_type TEXT DEFAULT 'custom',
                description TEXT DEFAULT '',
                player_name TEXT DEFAULT '',
                player_age INTEGER DEFAULT 18,
                created_at TEXT DEFAULT '',
                created_at_display TEXT DEFAULT '',
                last_saved_at TEXT DEFAULT '',
                last_saved_at_display TEXT DEFAULT '',
                world_def TEXT DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS narrative (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                world_id TEXT NOT NULL,
                entry_type TEXT DEFAULT 'narrative',
                day INTEGER DEFAULT 0,
                time TEXT DEFAULT '',
                text TEXT DEFAULT '',
                player_input TEXT DEFAULT '',
                event_type TEXT DEFAULT ''
            );
        """)
        self._migrate()
        self._conn.commit()

    def _migrate(self):
        cursor = self._conn.execute("PRAGMA table_info(worlds)")
        columns = {row[1] for row in cursor.fetchall()}
        if "created_at_display" not in columns:
            self._conn.execute("ALTER TABLE worlds ADD COLUMN created_at_display TEXT DEFAULT ''")
        if "last_saved_at_display" not in columns:
            self._conn.execute("ALTER TABLE worlds ADD COLUMN last_saved_at_display TEXT DEFAULT ''")
        self._conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()

    def upsert_world(self, world_id: str, world_name: str, world_type: str,
                     description: str, player_name: str, player_age: int,
                     created_at: str = "", world_def: dict = None):
        from datetime import datetime
        display = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._conn.execute(
            """INSERT INTO worlds (world_id, world_name, world_type, description, player_name, player_age, created_at, created_at_display, last_saved_at, last_saved_at_display, world_def)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(world_id) DO UPDATE SET
               world_name=excluded.world_name, description=excluded.description,
               player_name=excluded.player_name, player_age=excluded.player_age,
               last_saved_at=excluded.last_saved_at, last_saved_at_display=excluded.last_saved_at_display, world_def=excluded.world_def""",
            (world_id, world_name, world_type, description, player_name, player_age,
             created_at, display, created_at, display, json.dumps(world_def or {}, ensure_ascii=False))
        )
        self._conn.commit()

    def update_world_saved(self, world_id: str):
        from datetime import datetime
        now = datetime.now()
        self._conn.execute(
            "UPDATE worlds SET last_saved_at=?, last_saved_at_display=? WHERE world_id=?",
            (now.isoformat(), now.strftime("%Y-%m-%d %H:%M"), world_id)
        )
        self._conn.commit()

    def list_worlds(self) -> list[dict]:
        cursor = self._conn.execute(
            "SELECT * FROM worlds ORDER BY last_saved_at DESC"
        )
        return [dict(r) for r in cursor.fetchall()]

    def get_world(self, world_id: str) -> Optional[dict]:
        cursor = self._conn.execute("SELECT * FROM worlds WHERE world_id=?", (world_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def delete_world(self, world_id: str):
        self._conn.execute("DELETE FROM narrative WHERE world_id=?", (world_id,))
        self._conn.execute("DELETE FROM worlds WHERE world_id=?", (world_id,))
        self._conn.commit()

    def add_narrative(self, world_id: str, entry_type: str, day: int, time: str,
                      text: str, player_input: str = "", event_type: str = ""):
        self._conn.execute(
            "INSERT INTO narrative (world_id, entry_type, day, time, text, player_input, event_type) VALUES (?,?,?,?,?,?,?)",
            (world_id, entry_type, day, time, text, player_input, event_type)
        )
        self._conn.commit()

    def get_narrative(self, world_id: str, limit: int = 100) -> list[dict]:
        cursor = self._conn.execute(
            "SELECT entry_type as type, day, time, text, player_input, event_type FROM narrative WHERE world_id=? ORDER BY id DESC LIMIT ?",
            (world_id, limit)
        )
        return [dict(r) for r in reversed(cursor.fetchall())]

    def search_narrative(self, world_id: str, keyword: str, limit: int = 20) -> list[dict]:
        # [Bug M13] 转义 LIKE 通配符，防止关键字中的 % 和 _ 被误解析
        escaped_keyword = keyword.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        cursor = self._conn.execute(
            "SELECT entry_type as type, day, time, text, player_input FROM narrative WHERE world_id=? AND text LIKE ? ESCAPE '\\' ORDER BY day DESC LIMIT ?",
            (world_id, f"%{escaped_keyword}%", limit)
        )
        return [dict(r) for r in cursor.fetchall()]

    def get_day_narrative(self, world_id: str, day: int) -> list[dict]:
        cursor = self._conn.execute(
            "SELECT entry_type as type, day, time, text, player_input, event_type FROM narrative WHERE world_id=? AND day=? ORDER BY id",
            (world_id, day)
        )
        return [dict(r) for r in cursor.fetchall()]

    def get_stats(self, world_id: str) -> dict:
        total = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM narrative WHERE world_id=?", (world_id,)
        ).fetchone()["cnt"]
        days = self._conn.execute(
            "SELECT MAX(day) as max_day FROM narrative WHERE world_id=?", (world_id,)
        ).fetchone()["max_day"] or 0
        narratives = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM narrative WHERE world_id=? AND entry_type='narrative'", (world_id,)
        ).fetchone()["cnt"]
        return {"total_entries": total, "max_day": days, "narrative_count": narratives}
