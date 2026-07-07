"""
JWT 认证模块 — 太虚幻境云服务版
"""
import jwt
import secrets
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("chronoverse.auth")

_ALGORITHM = "HS256"
_TOKEN_EXPIRY_HOURS = 24
_SECRET_FILE = Path(__file__).parent.parent / ".jwt_secret"


def _get_or_create_secret() -> str:
    if _SECRET_FILE.exists():
        return _SECRET_FILE.read_text(encoding="utf-8").strip()
    secret = secrets.token_urlsafe(48)
    _SECRET_FILE.write_text(secret, encoding="utf-8")
    return secret


_SECRET = _get_or_create_secret()


def create_token(user_id: str, username: str, is_admin: bool = False, is_guest: bool = False) -> str:
    payload = {
        "user_id": user_id,
        "username": username,
        "is_admin": is_admin,
        "is_guest": is_guest,
        "exp": datetime.utcnow() + timedelta(hours=_TOKEN_EXPIRY_HOURS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, _SECRET, algorithm=_ALGORITHM)


def verify_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, _SECRET, algorithms=[_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        logger.warning("Token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning("Invalid token: %s", e)
        return None
