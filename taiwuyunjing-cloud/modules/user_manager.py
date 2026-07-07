"""
用户管理模块 — 太虚幻境云服务版
"""
import sqlite3
import uuid
import logging
from pathlib import Path
from datetime import datetime
import bcrypt

logger = logging.getLogger("chronoverse.user")

DB_PATH = Path(__file__).parent.parent / "data" / "users.db"


class UserManager:
    def __init__(self, db_path: str | Path = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0,
                is_guest INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                last_login TEXT,
                last_session_start TEXT,
                total_turns INTEGER DEFAULT 0,
                total_sessions INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS daily_usage (
                user_id TEXT NOT NULL,
                date TEXT NOT NULL,
                turns_used INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, date)
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            INSERT OR IGNORE INTO settings (key, value) VALUES
                ('daily_turn_limit', '3'),
                ('quota_enabled', '0'),
                ('max_concurrent_users', '12'),
                ('session_timeout_minutes', '30');
        """)
        conn.commit()
        conn.close()

    def create_user(self, username: str, password: str, is_admin: bool = False, is_guest: bool = False) -> dict | None:
        user_id = str(uuid.uuid4())
        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO users (id, username, password_hash, is_admin, is_guest) VALUES (?, ?, ?, ?, ?)",
                (user_id, username, password_hash, int(is_admin), int(is_guest))
            )
            conn.commit()
            logger.info("User created: %s (admin=%s, guest=%s)", username, is_admin, is_guest)
            return {"id": user_id, "username": username, "is_admin": is_admin, "is_guest": is_guest}
        except sqlite3.IntegrityError:
            return None
        finally:
            conn.close()

    def verify_user(self, username: str, password: str) -> dict | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? AND is_active = 1", (username,)
        ).fetchone()
        conn.close()
        if not row:
            return None
        if not bcrypt.checkpw(password.encode('utf-8'), row['password_hash'].encode('utf-8')):
            return None
        conn = self._get_conn()
        conn.execute(
            "UPDATE users SET last_login = datetime('now'), total_sessions = total_sessions + 1 WHERE id = ?",
            (row['id'],)
        )
        conn.commit()
        conn.close()
        return {
            "id": row['id'], "username": row['username'],
            "is_admin": bool(row['is_admin']), "is_guest": bool(row['is_guest']),
        }

    def create_guest(self) -> dict:
        guest_name = f"游客_{uuid.uuid4().hex[:6]}"
        guest_password = uuid.uuid4().hex
        user = self.create_user(guest_name, guest_password, is_guest=True)
        return user or {"id": "", "username": guest_name, "is_admin": False, "is_guest": True}

    def get_user(self, user_id: str) -> dict | None:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.close()
        if not row:
            return None
        return {
            "id": row['id'], "username": row['username'],
            "is_admin": bool(row['is_admin']), "is_guest": bool(row['is_guest']),
            "is_active": bool(row['is_active']),
            "total_turns": row['total_turns'], "total_sessions": row['total_sessions'],
            "created_at": row['created_at'], "last_login": row['last_login'],
        }

    def get_user_by_username(self, username: str) -> dict | None:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        conn.close()
        if not row:
            return None
        return {
            "id": row['id'], "username": row['username'],
            "is_admin": bool(row['is_admin']), "is_guest": bool(row['is_guest']),
            "is_active": bool(row['is_active']),
        }

    def get_all_users(self) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def toggle_user_active(self, user_id: str) -> bool:
        conn = self._get_conn()
        row = conn.execute("SELECT is_active FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            conn.close()
            return False
        new_val = 0 if row['is_active'] else 1
        conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (new_val, user_id))
        conn.commit()
        conn.close()
        return True

    def record_turn(self, user_id: str):
        today = datetime.now().strftime("%Y-%m-%d")
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO daily_usage (user_id, date, turns_used) VALUES (?, ?, 1)
            ON CONFLICT(user_id, date) DO UPDATE SET turns_used = turns_used + 1
        """, (user_id, today))
        conn.execute("UPDATE users SET total_turns = total_turns + 1 WHERE id = ?", (user_id,))
        conn.commit()
        conn.close()

    def get_today_usage(self, user_id: str) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        conn = self._get_conn()
        row = conn.execute(
            "SELECT turns_used FROM daily_usage WHERE user_id = ? AND date = ?",
            (user_id, today)
        ).fetchone()
        conn.close()
        return row['turns_used'] if row else 0

    def get_setting(self, key: str, default: str = "") -> str:
        conn = self._get_conn()
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        conn.close()
        return row['value'] if row else default

    def set_setting(self, key: str, value: str):
        conn = self._get_conn()
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        conn.close()

    def get_all_settings(self) -> dict:
        conn = self._get_conn()
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        conn.close()
        return {row['key']: row['value'] for row in rows}

    def init_admin(self, username: str, password: str) -> bool:
        conn = self._get_conn()
        row = conn.execute("SELECT id FROM users WHERE is_admin = 1").fetchone()
        conn.close()
        if row:
            return False
        return self.create_user(username, password, is_admin=True) is not None
