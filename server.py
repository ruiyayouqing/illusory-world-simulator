"""
太虚幻境 — 虚拟世界人生模拟器 服务入口
[v10] 闭环学习 + 多智能体协调 + 分层记忆
"""
from __future__ import annotations
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler('server.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("chronoverse")

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from modules.security import encrypt_config_keys
from routes.deps import BASE_DIR, get_meta_db, load_config, access_token as _deps_access_token
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

# [v9] 访问令牌 — 启动时生成，用于本地API认证
_access_token: str = ""
_token_file = BASE_DIR / ".access_token"


def _init_access_token():
    """初始化或读取访问令牌"""
    global _access_token, _deps_access_token
    if _token_file.exists():
        _access_token = _token_file.read_text(encoding="utf-8").strip()
    if not _access_token:
        _access_token = secrets.token_urlsafe(32)
        _token_file.write_text(_access_token, encoding="utf-8")
    # [v11] 同步到 deps 模块，供 WebSocket 等路由使用
    _deps_access_token = _access_token
    logger.info("Access token initialized.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_meta_db()
    config_path = BASE_DIR / "config.json"
    if config_path.exists():
        try:
            encrypt_config_keys(config_path)
        except Exception as e:
            logger.warning("Startup config encryption skipped: %s", e)
    _init_access_token()
    logger.info("太虚幻境 虚拟世界人生模拟器 started, MetaDB initialized")
    yield
    # [v10.5] 关闭引擎：保存游戏状态 + 停止后台任务队列，防止 worker task 泄漏
    from routes.deps import meta_db, get_engine
    if meta_db:
        meta_db.close()
    _engine = get_engine()
    if _engine:
        try:
            _engine.close()
        except Exception as e:
            logger.warning("Engine close failed during shutdown: %s", e)
    logger.info("太虚幻境 虚拟世界人生模拟器 shut down")


app = FastAPI(title="太虚幻境 - 虚拟世界人生模拟器", lifespan=lifespan)

# [v9安全修复] CORS从配置读取allowed_origins，默认仅允许本地
_config = load_config() or {}
_server_conf = _config.get("server", {})
_allowed_origins = _server_conf.get("allowed_origins", [
    "http://localhost:8004",
    "http://127.0.0.1:8004",
])

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# [v9] 安全中间件 — 保护敏感API端点
_SENSITIVE_PATHS = [
    "/api/config", "/api/save", "/api/slot", "/api/import-novel",
    "/api/create", "/api/load", "/api/generate-world",
    "/api/full-settings", "/api/settings", "/api/model-profiles",
    "/api/generate-image", "/api/character-card",
    "/api/upload-description", "/api/narrative-style/upload",
    "/api/delete", "/api/config/raw",
    # [v11] 补齐缺失的敏感端点
    "/api/input", "/api/state", "/api/saves", "/api/worlds",
    "/api/event", "/api/advance", "/api/experience",
    "/api/life-goal", "/api/better-options",
    "/api/narrative-history", "/api/group-chat",
    "/api/hundred-book",
    "/api/npc-prediction",
    "/api/lorebook", "/api/npc/card",
]


def _is_sensitive(path: str) -> bool:
    """[v9] 前缀匹配敏感端点"""
    return any(path == sp or path.startswith(sp + "/") or path.startswith(sp) for sp in _SENSITIVE_PATHS)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """[v9] 安全中间件：对敏感端点检查令牌，防止API密钥泄露"""
    path = request.url.path

    # 静态资源和非API路径不检查
    if not path.startswith("/api/"):
        return await call_next(request)

    # 对敏感端点检查令牌（如果令牌已设置）
    if _access_token and _is_sensitive(path):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if token != _access_token:
            return JSONResponse(
                {"error": "Unauthorized: invalid or missing access token"},
                status_code=401,
            )

    response = await call_next(request)
    return response


@app.get("/api/access-token")
async def get_access_token():
    """获取访问令牌（本地环境，无需认证）"""
    return {"access_token": _access_token}


static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

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

app.websocket("/ws/{client_id}")(websocket_endpoint)


def _open_browser(host: str, port: int):
    """启动后延迟打开浏览器"""
    import time
    import webbrowser
    time.sleep(1.5)
    url = f"http://{host}:{port}"
    try:
        webbrowser.open(url)
    except Exception:
        pass


if __name__ == "__main__":
    import uvicorn
    import threading
    config_path = BASE_DIR / "config.json"
    host = "127.0.0.1"
    port = 8000
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        server_conf = config.get("server", {})
        host = server_conf.get("host", host)
        port = server_conf.get("port", port)
    
    # 自动打开浏览器（如果环境变量没有禁用）
    if os.environ.get("AUTO_OPEN_BROWSER", "1") != "0":
        threading.Thread(target=_open_browser, args=(host, port), daemon=True).start()
    
    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=False,
        workers=1,
        loop="asyncio",
        use_colors=False,
    )
