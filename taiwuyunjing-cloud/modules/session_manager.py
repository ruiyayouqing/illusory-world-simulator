"""
会话管理模块 — 太虚幻境云服务版
控制并发用户数、会话超时、排队系统
"""
import threading
import time
import logging
from collections import OrderedDict
from typing import Optional

logger = logging.getLogger("chronoverse.session")


class SessionManager:
    """会话管理器：控制并发用户数、会话超时、排队"""

    def __init__(self, max_sessions: int = 12, session_timeout: int = 1800):
        self.max_sessions = max_sessions
        self.session_timeout = session_timeout  # 秒，默认 1800 = 30 分钟
        self._sessions: OrderedDict[str, dict] = OrderedDict()
        self._queue: list[str] = []
        self._lock = threading.Lock()

    def start_session(self, user_id: str, username: str) -> dict:
        """开始或刷新用户会话"""
        with self._lock:
            self._cleanup_expired()

            # 用户已有活跃会话，刷新
            if user_id in self._sessions:
                self._sessions[user_id].update({
                    "username": username,
                    "last_activity": time.time(),
                    "expires_at": time.time() + self.session_timeout,
                })
                self._sessions.move_to_end(user_id)
                return {"status": "active", "session": self._sessions[user_id]}

            # 有空位，直接进入
            if len(self._sessions) < self.max_sessions:
                session = {
                    "user_id": user_id,
                    "username": username,
                    "start_time": time.time(),
                    "last_activity": time.time(),
                    "expires_at": time.time() + self.session_timeout,
                }
                self._sessions[user_id] = session
                logger.info("Session started: %s, active: %d/%d",
                            username, len(self._sessions), self.max_sessions)
                return {"status": "active", "session": session}

            # 加入排队
            if user_id not in self._queue:
                self._queue.append(user_id)
            position = self._queue.index(user_id) + 1
            logger.info("User queued: %s, position: %d", username, position)
            return {
                "status": "queued",
                "position": position,
                "total_waiting": len(self._queue),
                "estimated_wait": position * 2,
            }

    def get_session(self, user_id: str) -> Optional[dict]:
        """获取用户会话信息"""
        with self._lock:
            self._cleanup_expired()
            if user_id in self._sessions:
                session = self._sessions[user_id]
                remaining = max(0, int(session["expires_at"] - time.time()))
                session["remaining_time"] = remaining
                return session
            if user_id in self._queue:
                return {
                    "status": "queued",
                    "position": self._queue.index(user_id) + 1,
                    "total_waiting": len(self._queue),
                }
            return None

    def end_session(self, user_id: str) -> bool:
        """结束用户会话"""
        with self._lock:
            if user_id in self._sessions:
                self._sessions.pop(user_id)
                self._promote_from_queue()
                logger.info("Session ended: %s", user_id)
                return True
            if user_id in self._queue:
                self._queue.remove(user_id)
                return True
            return False

    def heartbeat(self, user_id: str) -> dict:
        """心跳：更新会话活跃时间"""
        with self._lock:
            self._cleanup_expired()
            if user_id in self._sessions:
                session = self._sessions[user_id]
                session["last_activity"] = time.time()
                session["expires_at"] = time.time() + self.session_timeout
                self._sessions.move_to_end(user_id)
                remaining = max(0, int(session["expires_at"] - time.time()))
                return {"status": "active", "remaining_time": remaining}
            if user_id in self._queue:
                return {
                    "status": "queued",
                    "position": self._queue.index(user_id) + 1,
                    "total_waiting": len(self._queue),
                }
            return {"status": "expired"}

    def _cleanup_expired(self):
        """清理过期会话"""
        now = time.time()
        expired = [uid for uid, s in self._sessions.items() if now > s["expires_at"]]
        for uid in expired:
            self._sessions.pop(uid)
            logger.info("Session expired: %s", uid)
        for _ in expired:
            self._promote_from_queue()

    def _promote_from_queue(self):
        """从队列中提升用户到活跃会话"""
        while self._queue and len(self._sessions) < self.max_sessions:
            user_id = self._queue.pop(0)
            session = {
                "user_id": user_id,
                "start_time": time.time(),
                "last_activity": time.time(),
                "expires_at": time.time() + self.session_timeout,
            }
            self._sessions[user_id] = session
            logger.info("User promoted from queue: %s", user_id)

    def get_active_count(self) -> int:
        with self._lock:
            self._cleanup_expired()
            return len(self._sessions)

    def get_queue_length(self) -> int:
        with self._lock:
            return len(self._queue)

    def get_all_sessions(self) -> list[dict]:
        """获取所有活跃会话（管理后台用）"""
        with self._lock:
            self._cleanup_expired()
            now = time.time()
            sessions = []
            for uid, s in self._sessions.items():
                session = dict(s)
                session["remaining_time"] = max(0, int(s["expires_at"] - now))
                sessions.append(session)
            return sessions
