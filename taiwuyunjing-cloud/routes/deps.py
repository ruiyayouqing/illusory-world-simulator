"""
依赖注入模块 — 太虚幻境云服务版
支持多用户引擎隔离，通过 contextvars 实现透明路由
"""
from __future__ import annotations
import asyncio
import json
import logging
import threading
import contextvars
from pathlib import Path
from typing import Optional

from modules.game_engine import GameEngine
from modules.db.meta_db import WorldMetaDB

logger = logging.getLogger("chronoverse")

BASE_DIR = Path(__file__).parent.parent

# ===== 上下文变量：每请求隔离 =====
_current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar("current_user_id", default="")
_current_user_info: contextvars.ContextVar[dict] = contextvars.ContextVar("current_user_info", default={})

# ===== 全局服务实例（在 server.py 中初始化） =====
engine_pool = None
session_manager = None
user_manager = None
quota_manager = None

# ===== MetaDB 按用户缓存 =====
_meta_dbs: dict[str, WorldMetaDB] = {}
_meta_db_lock = threading.Lock()

# ===== WebSocket =====
active_connections: dict[str, "WebSocket"] = {}
ws_lock = asyncio.Lock()

# ===== 兼容性保留 =====
_engine_switch_lock = asyncio.Lock()
access_token: str = ""  # WebSocket 兼容
_config_cache: dict | None = None


def set_current_user(user_id: str, user_info: dict):
    """设置当前请求的用户上下文（由 auth 中间件调用）"""
    _current_user_id.set(user_id)
    _current_user_info.set(user_info)


def get_current_user_id() -> str:
    return _current_user_id.get()


def get_current_user_info() -> dict:
    return _current_user_info.get()


def get_engine() -> Optional[GameEngine]:
    """获取当前用户的引擎（透明路由，现有路由无需改动）"""
    user_id = _current_user_id.get()
    if not user_id or not engine_pool:
        return None
    return engine_pool.get_or_create(user_id)


def set_engine(e: GameEngine):
    """云版兼容：引擎由 engine_pool 管理，此函数为空操作"""
    pass


def get_meta_db() -> Optional[WorldMetaDB]:
    """获取当前用户的 MetaDB"""
    user_id = _current_user_id.get()
    if not user_id:
        return None
    with _meta_db_lock:
        if user_id not in _meta_dbs:
            user_data_dir = BASE_DIR / "data" / "user_data" / user_id
            _meta_dbs[user_id] = WorldMetaDB(str(user_data_dir / "chronoverse.db"))
        return _meta_dbs[user_id]


def require_engine() -> GameEngine:
    engine = get_engine()
    if not engine:
        raise RuntimeError("游戏未初始化")
    return engine


def load_config() -> dict:
    """加载服务端配置"""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    config_path = BASE_DIR / "config.json"
    if not config_path.exists():
        _config_cache = {}
        return _config_cache
    try:
        from modules.security import decrypt_config_keys
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        _config_cache = decrypt_config_keys(raw)
    except Exception as e:
        logger.warning("Failed to load config.json: %s", e)
        _config_cache = {}
    return _config_cache


def save_config(config: dict):
    """保存服务端配置（仅管理员可调用）"""
    global _config_cache
    config_path = BASE_DIR / "config.json"
    from modules.data.safe_io import atomic_write_json
    atomic_write_json(config_path, config)
    _config_cache = None
    # 更新引擎池配置
    if engine_pool:
        from modules.security import decrypt_config_keys
        engine_pool.server_config = decrypt_config_keys(config)
