"""
认证路由 — 太虚幻境云服务版
登录、注册、游客登录、用户信息、心跳、会话状态
"""
from __future__ import annotations
import logging
from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import deps
from .deps import (
    get_current_user_id, get_current_user_info,
    user_manager, session_manager, quota_manager,
)
from modules.auth import create_token

logger = logging.getLogger("chronoverse.auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])


# ===== 请求模型 =====
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=32)
    password: str = Field(..., min_length=1, max_length=128)


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=32)
    password: str = Field(..., min_length=6, max_length=128)


# ===== 路由 =====
@router.post("/login")
async def login(req: LoginRequest):
    """用户登录（管理员和普通用户同一入口）"""
    if not user_manager:
        return {"error": "服务未初始化"}

    user = user_manager.verify_user(req.username, req.password)
    if not user:
        return {"error": "用户名或密码错误"}

    # 排队时也需要 token，否则前端无法轮询会话状态
    token = create_token(
        user_id=user["id"],
        username=user["username"],
        is_admin=user["is_admin"],
        is_guest=user["is_guest"],
    )

    # 启动会话（可能排队）
    if session_manager:
        result = session_manager.start_session(user["id"], user["username"])
        if result["status"] == "queued":
            return {
                "status": "queued",
                "token": token,
                "user": {
                    "id": user["id"],
                    "username": user["username"],
                    "is_admin": user["is_admin"],
                    "is_guest": user["is_guest"],
                },
                "message": f"前方有 {result['position'] - 1} 人排队，请耐心等待",
                "position": result["position"],
                "total_waiting": result["total_waiting"],
                "estimated_wait": result.get("estimated_wait", 0),
            }

    # 获取用户配额
    quota_info = {}
    if quota_manager:
        quota_info = quota_manager.get_usage_info(user["id"])

    logger.info("User logged in: %s (admin=%s)", user["username"], user["is_admin"])
    return {
        "status": "active",
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "is_admin": user["is_admin"],
            "is_guest": user["is_guest"],
        },
        "quota": quota_info,
    }


@router.post("/register")
async def register(req: RegisterRequest):
    """用户注册"""
    if not user_manager:
        return {"error": "服务未初始化"}

    # 简单的用户名格式校验
    username = req.username.strip()
    if not username or " " in username:
        return {"error": "用户名不能包含空格"}

    user = user_manager.create_user(username, req.password, is_admin=False, is_guest=False)
    if not user:
        return {"error": "用户名已存在"}

    token = create_token(
        user_id=user["id"],
        username=user["username"],
        is_admin=False,
        is_guest=False,
    )

    # 注册成功后自动启动会话
    if session_manager:
        result = session_manager.start_session(user["id"], user["username"])
        if result["status"] == "queued":
            return {
                "status": "queued",
                "token": token,
                "user": {
                    "id": user["id"],
                    "username": user["username"],
                    "is_admin": False,
                    "is_guest": False,
                },
                "message": f"注册成功，前方有 {result['position'] - 1} 人排队",
                "position": result["position"],
                "total_waiting": result["total_waiting"],
            }

    quota_info = {}
    if quota_manager:
        quota_info = quota_manager.get_usage_info(user["id"])

    logger.info("User registered: %s", user["username"])
    return {
        "status": "active",
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "is_admin": False,
            "is_guest": False,
        },
        "quota": quota_info,
    }


@router.post("/guest")
async def guest_login():
    """游客登录"""
    if not user_manager:
        return {"error": "服务未初始化"}

    user = user_manager.create_guest()
    if not user or not user.get("id"):
        return {"error": "游客创建失败，请稍后重试"}

    token = create_token(
        user_id=user["id"],
        username=user["username"],
        is_admin=False,
        is_guest=True,
    )

    if session_manager:
        result = session_manager.start_session(user["id"], user["username"])
        if result["status"] == "queued":
            return {
                "status": "queued",
                "token": token,
                "user": {
                    "id": user["id"],
                    "username": user["username"],
                    "is_admin": False,
                    "is_guest": True,
                },
                "message": f"前方有 {result['position'] - 1} 人排队，请耐心等待",
                "position": result["position"],
                "total_waiting": result["total_waiting"],
            }

    quota_info = {}
    if quota_manager:
        quota_info = quota_manager.get_usage_info(user["id"])

    logger.info("Guest login: %s", user["username"])
    return {
        "status": "active",
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "is_admin": False,
            "is_guest": True,
        },
        "quota": quota_info,
    }


@router.get("/me")
async def get_me():
    """获取当前登录用户信息"""
    user_id = get_current_user_id()
    if not user_id:
        return {"error": "未登录"}

    info = get_current_user_info() or {}
    username = info.get("username", "")
    is_admin = info.get("is_admin", False)
    is_guest = info.get("is_guest", False)

    quota_info = {}
    if quota_manager:
        quota_info = quota_manager.get_usage_info(user_id)

    session_info = None
    if session_manager:
        session_info = session_manager.get_session(user_id)

    return {
        "id": user_id,
        "username": username,
        "is_admin": is_admin,
        "is_guest": is_guest,
        "quota": quota_info,
        "session": session_info,
    }


@router.post("/logout")
async def logout():
    """退出登录，结束会话"""
    user_id = get_current_user_id()
    if not user_id:
        return {"status": "ok"}

    info = get_current_user_info() or {}
    username = info.get("username", "")

    if session_manager:
        session_manager.end_session(user_id)
    if deps.engine_pool:
        deps.engine_pool.remove(user_id)

    logger.info("User logged out: %s", username)
    return {"status": "ok"}


@router.post("/heartbeat")
async def heartbeat():
    """心跳：刷新会话活跃时间"""
    user_id = get_current_user_id()
    if not user_id:
        return {"status": "expired"}

    if not session_manager:
        return {"status": "expired"}

    result = session_manager.heartbeat(user_id)
    if result["status"] == "active":
        # 返回剩余时间
        quota_info = {}
        if quota_manager:
            quota_info = quota_manager.get_usage_info(user_id)
        return {
            "status": "active",
            "remaining_time": result.get("remaining_time", 0),
            "quota": quota_info,
        }
    elif result["status"] == "queued":
        return {
            "status": "queued",
            "message": f"前方有 {result['position'] - 1} 人排队",
            "position": result["position"],
            "total_waiting": result["total_waiting"],
        }
    return result


@router.get("/session")
async def get_session_status():
    """查询会话状态"""
    user_id = get_current_user_id()
    if not user_id:
        return {"status": "expired"}

    if not session_manager:
        return {"status": "expired"}

    session = session_manager.get_session(user_id)
    if not session:
        return {"status": "expired"}

    if session.get("status") == "queued":
        return {
            "status": "queued",
            "message": f"前方有 {session['position'] - 1} 人排队",
            "position": session["position"],
            "total_waiting": session["total_waiting"],
        }

    quota_info = {}
    if quota_manager:
        quota_info = quota_manager.get_usage_info(user_id)

    return {
        "status": "active",
        "remaining_time": session.get("remaining_time", 0),
        "start_time": session.get("start_time", 0),
        "quota": quota_info,
    }


@router.get("/queue")
async def get_queue_status():
    """查询排队状态"""
    user_id = get_current_user_id()
    if not user_manager or not session_manager:
        return {"active": 0, "queued": 0, "position": 0}

    position = 0
    if user_id:
        session = session_manager.get_session(user_id)
        if session and session.get("status") == "queued":
            position = session.get("position", 0)

    return {
        "active_count": session_manager.get_active_count(),
        "max_sessions": session_manager.max_sessions,
        "queue_length": session_manager.get_queue_length(),
        "position": position,
    }


@router.get("/check")
async def check_username(username: str):
    """检查用户名是否可用"""
    if not user_manager:
        return {"available": False, "error": "服务未初始化"}

    user = user_manager.get_user_by_username(username)
    return {"available": user is None}
