from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, Response

from .deps import BASE_DIR

router = APIRouter()

BASE_DIR_RESOLVED = BASE_DIR.resolve()
ALLOWED_CSS_DIR = (BASE_DIR_RESOLVED / "frontend" / "css").resolve()
ALLOWED_JS_DIR = (BASE_DIR_RESOLVED / "frontend" / "js").resolve()
ALLOWED_IMG_DIR = (BASE_DIR_RESOLVED / "static" / "images").resolve()


def _safe_path(base_dir: Path, path: str) -> Path | None:
    """安全地解析路径，防止路径遍历"""
    target = (base_dir / path).resolve()
    try:
        target.relative_to(base_dir)
    except ValueError:
        return None
    return target if target.exists() else None


# 云版：根路由 / 由 server.py 处理（返回登录页）
# 静态资源由 StaticFiles 挂载处理

@router.get("/css/{path:path}")
async def serve_css(path: str):
    safe = _safe_path(ALLOWED_CSS_DIR, path)
    if not safe:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(safe)


@router.get("/js/{path:path}")
async def serve_js(path: str):
    safe = _safe_path(ALLOWED_JS_DIR, path)
    if not safe:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(safe)


@router.get("/images/{path:path}")
async def serve_image(path: str):
    safe = _safe_path(ALLOWED_IMG_DIR, path)
    if not safe:
        return Response(status_code=404)
    return FileResponse(safe)


@router.get("/access-token")
async def get_access_token():
    """云版兼容：返回空 token（JWT 由前端管理）"""
    return {"access_token": ""}
