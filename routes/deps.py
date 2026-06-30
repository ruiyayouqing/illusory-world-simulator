from __future__ import annotations
import asyncio
import json
import logging
import threading
from pathlib import Path
from typing import Optional

from modules.game_engine import GameEngine
from modules.db.meta_db import WorldMetaDB

logger = logging.getLogger("chronoverse")

BASE_DIR = Path(__file__).parent.parent
engine: Optional[GameEngine] = None
meta_db: Optional[WorldMetaDB] = None
active_connections: dict[str, "WebSocket"] = {}  # [v9] client_id -> WebSocket
ws_lock: asyncio.Lock = asyncio.Lock()  # [v9] WebSocket并发保护锁

# [v9] Bug M4b: MetaDB 单例初始化锁，防止并发重复创建
_meta_db_lock = threading.Lock()

# [v10.5] engine 切换锁 — 保护 /create、/load 等会替换全局 engine 的操作
# 防止并发加载/创建导致全局 engine 指向半初始化的实例
_engine_switch_lock = asyncio.Lock()

# [v11] 访问令牌，由 server.py 初始化，供 WebSocket 等需要鉴权的路径使用
access_token: str = ""


def get_meta_db() -> WorldMetaDB:
    global meta_db
    if meta_db is None:
        with _meta_db_lock:
            if meta_db is None:
                meta_db = WorldMetaDB(BASE_DIR / "data" / "chronoverse.db")
    return meta_db


def get_engine() -> Optional[GameEngine]:
    return engine


def set_engine(e: GameEngine):
    global engine
    engine = e
    # [v10.5+] 启动后台任务队列的 worker（必须在主事件循环中调用，否则 worker 创建在从未启动的新 loop 上）
    if hasattr(e, 'task_queue') and e.task_queue is not None:
        try:
            e.task_queue.start()
        except Exception:
            pass


def require_engine():
    if not engine:
        raise RuntimeError("游戏未初始化")
    return engine


def load_config() -> dict:
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    config_path = BASE_DIR / "config.json"
    if not config_path.exists():
        _config_cache = {}
        return _config_cache
    _config_cache = json.loads(config_path.read_text(encoding="utf-8"))
    return _config_cache


def save_config(config: dict):
    global _config_cache
    config_path = BASE_DIR / "config.json"
    from modules.data.safe_io import atomic_write_json
    atomic_write_json(config_path, config)
    _config_cache = None
    if engine:
        engine.invalidate_config_cache()


_config_cache: dict | None = None
