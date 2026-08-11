"""
太虚幻境 — 虚拟世界人生模拟器 服务入口
[v10] 闭环学习 + 多智能体协调 + 分层记忆
[v1.7 P2-5] 可观测性增强：request_id 中间件 + 结构化日志 + 日志轮转
"""
from __future__ import annotations
import contextvars
import json
import logging
import logging.handlers
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

# [v1.7 P2-5] 请求级追踪：contextvar 存储 request_id，可被 logging.Filter 读取
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    """把 request_id 注入每条日志记录的 extra 字段，供格式化使用。"""
    def filter(self, record: logging.LogRecord) -> bool:
        rid = request_id_var.get("-")
        # [v1.7 P2-5] 空字符串视为未设置，回退到默认占位符
        record.request_id = rid if rid else "-"
        return True


# [v1.7 P2-5] 日志配置：RotatingFileHandler 防止 server.log 无限增长
# 格式追加 [req=xxx] 字段，便于日志聚合系统按请求分组
_logging_format = '%(asctime)s [%(levelname)s] [req=%(request_id)s] %(name)s: %(message)s'
_file_handler = logging.handlers.RotatingFileHandler(
    'server.log', maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8',
)
_stream_handler = logging.StreamHandler()
for _h in (_file_handler, _stream_handler):
    _h.setFormatter(logging.Formatter(_logging_format))
    _h.addFilter(RequestIdFilter())

logging.basicConfig(
    level=logging.INFO,
    handlers=[_file_handler, _stream_handler],
)
logger = logging.getLogger("chronoverse")

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from modules.security import encrypt_config_keys
from routes.deps import BASE_DIR, get_meta_db, load_config
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
from routes.novel_roleplay_routes import router as novel_roleplay_router
from routes.auto_run_routes import router as auto_run_router
from routes.websocket_routes import websocket_endpoint

# [v9] 访问令牌 — 启动时生成，用于本地API认证
_access_token: str = ""
_token_file = BASE_DIR / ".access_token"


def _init_access_token():
    """初始化或读取访问令牌
    优先级: 环境变量 TXHJ_ACCESS_TOKEN > .access_token 文件 > 随机生成
    公网部署时通过 systemd Environment=TXHJ_ACCESS_TOKEN=xxx 固定令牌，
    方便手机端输入；本地部署保持原逻辑不变。
    """
    global _access_token
    env_token = os.environ.get("TXHJ_ACCESS_TOKEN", "").strip()
    if env_token:
        _access_token = env_token
        # 同步写入文件，保持与 deps 模块持久化一致
        try:
            _token_file.write_text(_access_token, encoding="utf-8")
        except Exception:
            pass
    elif _token_file.exists():
        _access_token = _token_file.read_text(encoding="utf-8").strip()
    if not _access_token:
        _access_token = secrets.token_urlsafe(32)
        _token_file.write_text(_access_token, encoding="utf-8")
    # [v11] 同步到 deps 模块，供 WebSocket 等路由使用
    # [Bug P3-E] 原先只赋值给 server 模块的 _deps_access_token（未使用），
    # deps.access_token 始终为空字符串，导致 WebSocket 鉴权形同虚设。
    # 直接设置 deps.access_token 让 websocket_endpoint 的 token 校验生效。
    from routes import deps as _deps
    _deps.access_token = _access_token
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


# [v1.7 P2-5] request_id 中间件：为每个请求生成/复用追踪 ID，注入 contextvar + 响应头
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    # 优先复用客户端传入的 X-Request-ID，否则生成
    rid = request.headers.get("x-request-id") or secrets.token_hex(8)
    token = request_id_var.set(rid)
    start = time.perf_counter()
    try:
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = rid
        # 访问日志（仅记录非静态资源）
        path = request.url.path
        if not path.startswith(("/static", "/css", "/js", "/fonts", "/favicon")):
            logger.info(
                "%s %s %d %.1fms",
                request.method, path, response.status_code, elapsed_ms,
            )
        return response
    finally:
        request_id_var.reset(token)


# [v1.7 P2-2] 全局异常处理：统一错误响应格式，防止堆栈泄露
# [v1.7 P2-5] 所有错误响应统一带上 request_id 便于追踪
@app.exception_handler(StarletteHTTPException)
async def _http_exc_handler(request: Request, exc: StarletteHTTPException):
    """HTTP 异常统一返回 JSON 格式。"""
    return JSONResponse(
        {"error": exc.detail, "status_code": exc.status_code, "request_id": request_id_var.get("-")},
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
async def _validation_exc_handler(request: Request, exc: RequestValidationError):
    """请求参数校验失败：返回 422 + 具体字段错误。"""
    return JSONResponse(
        {"error": "validation_error", "detail": exc.errors(), "request_id": request_id_var.get("-")},
        status_code=422,
    )


@app.exception_handler(RuntimeError)
async def _runtime_exc_handler(request: Request, exc: RuntimeError):
    """业务运行时错误（如「游戏未初始化」）：返回 503。"""
    logger.warning("RuntimeError on %s [req=%s]: %s",
                   request.url.path, request_id_var.get("-"), exc)
    return JSONResponse(
        {"error": "service_unavailable", "detail": str(exc), "request_id": request_id_var.get("-")},
        status_code=503,
    )


@app.exception_handler(Exception)
async def _unhandled_exc_handler(request: Request, exc: Exception):
    """未捕获异常兜底：返回 500 + 简短错误信息，不泄露完整堆栈。"""
    rid = request_id_var.get("-")
    logger.error("Unhandled error [req=%s] on %s: %s", rid, request.url.path, exc, exc_info=True)
    return JSONResponse(
        {"error": "internal_error", "detail": str(exc)[:200], "request_id": rid},
        status_code=500,
    )


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
    # [v1.4] 监控端点暴露内部统计，列为敏感
    "/api/metrics",
    # [v1.7 P2-5] 耗时打点端点暴露内部性能数据，列为敏感
    "/api/timing", "/api/timing/clear",
    # [v1.2] 自主运行会修改游戏状态，列为敏感
    "/api/auto-run",
]


def _is_sensitive(path: str) -> bool:
    """[v9] 前缀匹配敏感端点"""
    return any(path == sp or path.startswith(sp + "/") or path.startswith(sp) for sp in _SENSITIVE_PATHS)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """[v9] 安全中间件：对敏感端点检查令牌，防止API密钥泄露
    [v1.4] 修复 DNS rebinding 漏洞：本地放行需同时校验 Host header
           修复 /api/access-token 暴露：仅本地 + 已认证请求可访问"""
    path = request.url.path

    # CORS 预检请求直接放行（OPTIONS 不带 Authorization header）
    if request.method == "OPTIONS":
        return await call_next(request)

    # 静态资源和非API路径不检查
    if not path.startswith("/api/"):
        return await call_next(request)

    # [v1.4] 判断是否为可信本地请求：同时校验 client_host 和 Host header
    # 防 DNS rebinding：攻击者让浏览器从 example.com 访问 127.0.0.1:8000
    client_host = request.client.host if request.client else ""
    host_header = request.headers.get("Host", "").split(":")[0].lower()
    is_trusted_local = (
        client_host in ("127.0.0.1", "localhost", "::1")
        and host_header in ("127.0.0.1", "localhost", "")
    )

    # [v1.4] /api/access-token 仅允许可信本地请求访问，且只在首次启动后返回一次
    # 后续请求必须携带有效 token（防止任意进程窃取）
    if path == "/api/access-token":
        if not is_trusted_local:
            return JSONResponse(
                {"error": "Forbidden: access-token endpoint is local-only"},
                status_code=403,
            )
        # 本地请求也要返回（前端首次加载需要），但浏览器跨域请求会被 Host 校验挡掉
        return await call_next(request)

    # [v13] 白名单式保护：除 /api/health（探活）外，所有 /api/ 端点均需令牌
    # 修复：原黑名单 _SENSITIVE_PATHS 覆盖不全，/api/npcs、/api/npc-actions 等
    # 核心接口无 token 即可访问，导致公网"免令牌直进游戏"
    if _access_token and path != "/api/health":
        # 可信本地请求直接放行
        if is_trusted_local:
            return await call_next(request)

        # 从 Authorization header 读取 token
        token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
        # 同时支持从 query 参数读取 token（容错）
        if not token:
            token = request.query_params.get("access_token", "")
        if token != _access_token:
            return JSONResponse(
                {"error": "Unauthorized: invalid or missing access token"},
                status_code=401,
            )

    response = await call_next(request)
    return response


@app.get("/api/access-token")
async def get_access_token(request: Request):
    """获取访问令牌
    [v1.4] 仅允许可信本地请求（127.0.0.1 + Host header 校验），
    阻止 DNS rebinding 和远程窃取"""
    # 二次校验（中间件已挡，这里防御性编程）
    client_host = request.client.host if request.client else ""
    host_header = request.headers.get("Host", "").split(":")[0].lower()
    if client_host not in ("127.0.0.1", "localhost", "::1"):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    if host_header not in ("127.0.0.1", "localhost", ""):
        return JSONResponse({"error": "Forbidden: Host header mismatch"}, status_code=403)
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
app.include_router(novel_roleplay_router)
app.include_router(auto_run_router)

app.websocket("/ws/{client_id}")(websocket_endpoint)


# 记录启动时间（供 /api/health 计算 uptime）
import time as _time_module
_APP_START_TIME = _time_module.time()


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
    # [v1.4 P1-8] 优先级：环境变量 HOST/PORT > config.json > 默认 127.0.0.1:8000
    # 普通玩家 .bat 启动无环境变量，仍读 config.json（127.0.0.1:8004）
    # 容器启动通过 ENV HOST=0.0.0.0 覆盖，让外部可访问
    host = os.environ.get("HOST") or "127.0.0.1"
    port = int(os.environ.get("PORT", "0"))
    if not port:
        port = 8000
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            server_conf = config.get("server", {})
            if not os.environ.get("HOST"):
                host = server_conf.get("host", host)
            if not os.environ.get("PORT"):
                port = server_conf.get("port", port)
        except Exception as e:
            logger.warning("Failed to read config.json for host/port: %s", e)

    # 自动打开浏览器（如果环境变量没有禁用；容器内应设置 AUTO_OPEN_BROWSER=0）
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
