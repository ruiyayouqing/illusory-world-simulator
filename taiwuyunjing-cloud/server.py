"""
太虚幻境 — 虚拟世界人生模拟器 云服务版
多用户在线 Demo 服务入口
"""
from __future__ import annotations
import json
import logging
import asyncio
import os
from pathlib import Path
from contextlib import asynccontextmanager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler('server.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("chronoverse")

from fastapi import FastAPI, Request, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse

from routes.deps import BASE_DIR, set_current_user, get_current_user_id
from routes.deps import engine_pool, session_manager, user_manager, quota_manager
from modules.auth import create_token, verify_token
from modules.user_manager import UserManager
from modules.session_manager import SessionManager
from modules.quota_manager import QuotaManager
from modules.engine_pool import EnginePool

# ===== 路由导入 =====
from routes.static_routes import router as static_router
from routes.config_routes import router as config_router
from routes.game_routes import router as game_router
from routes.npc_routes import router as npc_router
from routes.narrative_routes import router as narrative_router
from routes.player_routes import router as player_router
from routes.systems_routes import router as systems_router
from routes.prediction_routes import router as prediction_router
from routes.lorebook_routes import router as lorebook_router
from routes.character_card_routes import router as character_card_router
from routes.websocket_routes import websocket_endpoint
from routes.auth_routes import router as auth_router
from routes.admin_routes import router as admin_router


# ===== 全局服务实例 =====
_engine_pool: EnginePool = None
_session_manager: SessionManager = None
_user_manager: UserManager = None
_quota_manager: QuotaManager = None


async def _cleanup_background():
    """后台任务：定期清理空闲引擎和过期会话"""
    while True:
        await asyncio.sleep(300)  # 每 5 分钟
        try:
            if _engine_pool:
                _engine_pool.cleanup_idle()
            if _session_manager:
                logger.debug("Sessions: %d active, %d queued",
                             _session_manager.get_active_count(),
                             _session_manager.get_queue_length())
        except Exception as e:
            logger.warning("Cleanup task error: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _engine_pool, _session_manager, _user_manager, _quota_manager

    # 初始化用户管理
    _user_manager = UserManager()
    _user_manager.init_admin("ruiyayouqing", "luopingwan")
    logger.info("UserManager initialized, admin ensured")

    # 初始化会话管理
    max_sessions = int(_user_manager.get_setting("max_concurrent_users", "12"))
    timeout_min = int(_user_manager.get_setting("session_timeout_minutes", "30"))
    _session_manager = SessionManager(
        max_sessions=max_sessions,
        session_timeout=timeout_min * 60,
    )
    logger.info("SessionManager initialized: max=%d, timeout=%dmin", max_sessions, timeout_min)

    # 初始化配额管理
    _quota_manager = QuotaManager(_user_manager)
    logger.info("QuotaManager initialized")

    # 加载服务端配置
    from routes.deps import load_config
    config = load_config()

    # 初始化引擎池
    _engine_pool = EnginePool(
        base_dir=BASE_DIR,
        server_config=config,
        max_engines=10,
        idle_timeout=timeout_min * 60,
    )
    logger.info("EnginePool initialized: max_engines=10")

    # 注入到 deps 模块
    import routes.deps as deps
    deps.engine_pool = _engine_pool
    deps.session_manager = _session_manager
    deps.user_manager = _user_manager
    deps.quota_manager = _quota_manager

    # 启动后台清理任务
    cleanup_task = asyncio.create_task(_cleanup_background())

    logger.info("太虚幻境 云服务版 started")
    yield

    # 关闭
    cleanup_task.cancel()
    if _engine_pool:
        for uid in list(_engine_pool._engines.keys()):
            _engine_pool.remove(uid)
    logger.info("太虚幻境 云服务版 shut down")


app = FastAPI(title="太虚幻境 - 云服务版", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== 公开路径（不需要认证） =====
_PUBLIC_PATHS = {
    "/", "/index.html", "/game.html", "/admin.html",
    "/favicon.ico",
    "/api/auth/login", "/api/auth/register", "/api/auth/guest",
    "/api/auth/check",
    "/api/health",
}

# 仅需认证但不需要活跃会话的路径
_AUTH_ONLY_PATHS = {
    "/api/auth/me", "/api/auth/logout", "/api/auth/heartbeat",
    "/api/auth/queue", "/api/auth/session",
    "/api/admin/health",
}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """JWT 认证中间件 + 会话检查"""
    path = request.url.path

    # 静态资源和公开路径不需要认证
    if not path.startswith("/api/") or path in _PUBLIC_PATHS:
        return await call_next(request)

    # 提取 JWT
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else ""

    if not token:
        return JSONResponse({"error": "未登录", "code": "no_token"}, status_code=401)

    payload = verify_token(token)
    if not payload:
        return JSONResponse({"error": "登录已过期，请重新登录", "code": "token_expired"}, status_code=401)

    user_id = payload.get("user_id", "")
    if not user_id:
        return JSONResponse({"error": "无效的令牌", "code": "invalid_token"}, status_code=401)

    # 设置上下文
    set_current_user(user_id, payload)

    # 管理员路径检查
    if path.startswith("/api/admin/"):
        if not payload.get("is_admin"):
            return JSONResponse({"error": "无管理员权限", "code": "not_admin"}, status_code=403)

    # 游戏相关路径需要检查会话
    if path not in _AUTH_ONLY_PATHS and not path.startswith("/api/admin/"):
        session = _session_manager.get_session(user_id) if _session_manager else None
        if not session:
            return JSONResponse({"error": "会话已过期，请重新登录", "code": "session_expired"}, status_code=403)
        if isinstance(session.get("status"), str) and session["status"] == "queued":
            return JSONResponse({
                "error": "正在排队中，请耐心等待",
                "code": "queued",
                "position": session.get("position", 0),
                "total_waiting": session.get("total_waiting", 0),
            }, status_code=429)

    response = await call_next(request)
    return response


# ===== 静态文件 =====
static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

frontend_dir = BASE_DIR / "frontend"
app.mount("/css", StaticFiles(directory=str(frontend_dir / "css")), name="css")
app.mount("/js", StaticFiles(directory=str(frontend_dir / "js")), name="js")


# ===== 页面路由 =====
@app.get("/", response_class=HTMLResponse)
async def login_page():
    return FileResponse(str(frontend_dir / "index.html"))


@app.get("/game", response_class=HTMLResponse)
async def game_page():
    return FileResponse(str(frontend_dir / "game.html"))


@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    return FileResponse(str(frontend_dir / "admin.html"))


# ===== API 路由注册 =====
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(static_router)
app.include_router(config_router)
app.include_router(game_router)
app.include_router(npc_router)
app.include_router(narrative_router)
app.include_router(player_router)
app.include_router(systems_router)
app.include_router(prediction_router)
app.include_router(lorebook_router)
app.include_router(character_card_router)

# WebSocket
@app.websocket("/ws/{client_id}")
async def ws_endpoint(websocket: WebSocket, client_id: str):
    # WebSocket 认证：通过 query 参数传递 token
    token = websocket.query_params.get("token", "")
    payload = verify_token(token) if token else None
    if not payload:
        await websocket.close(code=4001, reason="未认证")
        return
    set_current_user(payload.get("user_id", ""), payload)
    await websocket_endpoint(websocket, client_id)


if __name__ == "__main__":
    import uvicorn

    host = "0.0.0.0"
    port = 8004

    config_path = BASE_DIR / "config.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            server_conf = config.get("server", {})
            host = server_conf.get("host", host)
            port = server_conf.get("port", port)
        except Exception:
            pass

    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=False,
        workers=1,
        loop="asyncio",
        use_colors=False,
    )
