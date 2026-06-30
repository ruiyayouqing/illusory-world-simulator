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


def _get_access_token() -> str:
    """获取访问令牌"""
    token_file = BASE_DIR / ".access_token"
    if token_file.exists():
        return token_file.read_text(encoding="utf-8").strip()
    return ""


@router.get("/", response_class=HTMLResponse)
async def root():
    html_path = BASE_DIR / "index.html"
    html_content = html_path.read_text(encoding="utf-8")
    token = _get_access_token()
    inject_script = f'<script>window.ACCESS_TOKEN = "{token}";</script>'
    html_content = html_content.replace("</head>", inject_script + "\n</head>")
    return HTMLResponse(content=html_content)


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
