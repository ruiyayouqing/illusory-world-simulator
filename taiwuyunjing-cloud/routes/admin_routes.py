"""
管理后台路由 — 太虚幻境云服务版
用户管理、系统设置、统计监控
"""
from __future__ import annotations
import logging
from fastapi import APIRouter
from pydantic import BaseModel

from .deps import user_manager, session_manager, quota_manager, engine_pool

logger = logging.getLogger("chronoverse.admin")
router = APIRouter(prefix="/api/admin", tags=["admin"])


# ===== 请求模型 =====
class UpdateSettingsRequest(BaseModel):
    daily_turn_limit: int | None = None
    quota_enabled: bool | None = None
    max_concurrent_users: int | None = None
    session_timeout_minutes: int | None = None


class UpdateUserRequest(BaseModel):
    is_active: bool | None = None


# ===== 路由 =====
@router.get("/stats")
async def get_stats():
    """系统统计信息"""
    if not user_manager or not session_manager:
        return {"error": "服务未初始化"}

    all_users = user_manager.get_all_users()
    total_users = len(all_users)
    active_users = sum(1 for u in all_users if u.get("is_active"))
    guest_users = sum(1 for u in all_users if u.get("is_guest"))
    real_users = total_users - guest_users

    active_sessions = session_manager.get_active_count()
    queue_length = session_manager.get_queue_length()

    # 引擎池状态
    pool_status = engine_pool.get_pool_status() if engine_pool else {"total": 0, "max": 0}

    settings = user_manager.get_all_settings()

    return {
        "users": {
            "total": total_users,
            "active": active_users,
            "real": real_users,
            "guest": guest_users,
            "disabled": total_users - active_users,
        },
        "sessions": {
            "active": active_sessions,
            "queued": queue_length,
            "max": session_manager.max_sessions,
        },
        "engines": pool_status,
        "settings": settings,
    }


@router.get("/users")
async def list_users():
    """获取所有用户列表"""
    if not user_manager:
        return {"error": "服务未初始化", "users": []}

    users = user_manager.get_all_users()
    today_usage = {}
    for u in users:
        uid = u.get("id", "")
        u["today_turns"] = user_manager.get_today_usage(uid)
        # 隐藏密码哈希
        u.pop("password_hash", None)

    return {"users": users}


@router.post("/users/{user_id}/toggle")
async def toggle_user(user_id: str):
    """启用/禁用用户账号"""
    if not user_manager:
        return {"error": "服务未初始化"}

    # 不允许禁用管理员
    user = user_manager.get_user(user_id)
    if user and user.get("is_admin"):
        return {"error": "不能禁用管理员账号"}

    ok = user_manager.toggle_user_active(user_id)
    if not ok:
        return {"error": "用户不存在"}

    user = user_manager.get_user(user_id)
    logger.info("User toggled: %s -> active=%s", user_id, user.get("is_active"))
    return {"status": "ok", "user": user}


@router.post("/users/{user_id}/kick")
async def kick_user(user_id: str):
    """踢出用户会话（管理员强制下线）"""
    if not session_manager:
        return {"error": "服务未初始化"}

    session_manager.end_session(user_id)
    if engine_pool:
        engine_pool.remove(user_id)
    logger.info("User kicked: %s", user_id)
    return {"status": "ok"}


@router.get("/settings")
async def get_settings():
    """获取系统设置"""
    if not user_manager:
        return {"error": "服务未初始化"}

    settings = user_manager.get_all_settings()
    return {"settings": settings}


@router.post("/settings")
async def update_settings(req: UpdateSettingsRequest):
    """更新系统设置"""
    if not user_manager:
        return {"error": "服务未初始化"}

    updated = []
    if req.daily_turn_limit is not None:
        if req.daily_turn_limit < 1 or req.daily_turn_limit > 999:
            return {"error": "每日轮数上限必须在 1-999 之间"}
        user_manager.set_setting("daily_turn_limit", str(req.daily_turn_limit))
        updated.append("daily_turn_limit")

    if req.quota_enabled is not None:
        user_manager.set_setting("quota_enabled", "1" if req.quota_enabled else "0")
        updated.append("quota_enabled")

    if req.max_concurrent_users is not None:
        if req.max_concurrent_users < 1 or req.max_concurrent_users > 100:
            return {"error": "最大并发数必须在 1-100 之间"}
        user_manager.set_setting("max_concurrent_users", str(req.max_concurrent_users))
        if session_manager:
            session_manager.max_sessions = req.max_concurrent_users
        updated.append("max_concurrent_users")

    if req.session_timeout_minutes is not None:
        if req.session_timeout_minutes < 1 or req.session_timeout_minutes > 1440:
            return {"error": "会话超时必须在 1-1440 分钟之间"}
        user_manager.set_setting("session_timeout_minutes", str(req.session_timeout_minutes))
        if session_manager:
            session_manager.session_timeout = req.session_timeout_minutes * 60
        if engine_pool:
            engine_pool.idle_timeout = req.session_timeout_minutes * 60
        updated.append("session_timeout_minutes")

    logger.info("Settings updated: %s", updated)
    return {"status": "ok", "updated": updated}


@router.get("/sessions")
async def list_sessions():
    """获取所有活跃会话"""
    if not session_manager:
        return {"error": "服务未初始化", "sessions": []}

    sessions = session_manager.get_all_sessions()
    # 附加用户信息
    for s in sessions:
        uid = s.get("user_id", "")
        if user_manager:
            user = user_manager.get_user(uid)
            if user:
                s["username"] = user.get("username", "")
                s["is_guest"] = user.get("is_guest", False)

    return {"sessions": sessions}


@router.get("/health")
async def health_check():
    """服务健康检查"""
    return {
        "status": "ok",
        "services": {
            "user_manager": user_manager is not None,
            "session_manager": session_manager is not None,
            "quota_manager": quota_manager is not None,
            "engine_pool": engine_pool is not None,
        },
    }
