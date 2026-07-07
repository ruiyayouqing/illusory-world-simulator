from __future__ import annotations
import base64
import hashlib
import json
import logging
import os
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from .data.safe_io import atomic_write_json

logger = logging.getLogger("chronoverse.security")

_KEY_FILE = ".secret_key"


def _get_or_create_key() -> bytes:
    key_path = Path(__file__).parent.parent / _KEY_FILE
    if key_path.exists():
        return base64.b64decode(key_path.read_text().strip())
    key = os.urandom(32)
    from .data.safe_io import atomic_write_text
    atomic_write_text(key_path, base64.b64encode(key).decode(), backup=False)
    try:
        os.chmod(key_path, 0o600)
    except (OSError, AttributeError):
        pass  # Windows 不支持 Unix 权限位
    return key


_master_key = None


def _key() -> bytes:
    global _master_key
    if _master_key is None:
        _master_key = _get_or_create_key()
    return _master_key


def encrypt_value(plaintext: str) -> str:
    if not plaintext:
        return ""
    iv = os.urandom(16)
    cipher = AES.new(_key(), AES.MODE_CBC, iv)
    ct = cipher.encrypt(pad(plaintext.encode("utf-8"), AES.block_size))
    return base64.b64encode(iv + ct).decode("ascii")


def decrypt_value(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        raw = base64.b64decode(ciphertext)
        if len(raw) < 17:
            logger.error("Decryption failed: Invalid ciphertext length (too short)")
            return ""
        iv, ct = raw[:16], raw[16:]
        if len(iv) != 16:
            logger.error("Decryption failed: Incorrect IV length (it must be 16 bytes long)")
            return ""
        cipher = AES.new(_key(), AES.MODE_CBC, iv)
        return unpad(cipher.decrypt(ct), AES.block_size).decode("utf-8")
    except ValueError as e:
        logger.error("Decryption failed: %s", e)
        return ""
    except Exception as e:
        logger.error("Decryption failed: %s", e)
        return ""


def _encrypt_config_dict(config: dict) -> bool:
    """在内存中对 config dict 的 api_key 字段加密，返回是否有变更"""
    changed = False
    for section in ("llm", "image", "embedding", "cheap_llm", "dialogue_llm"):
        if section in config:
            for key_name in ("api_key",):
                val = config[section].get(key_name, "")
                if val and not val.startswith("enc:"):
                    config[section][key_name] = "enc:" + encrypt_value(val)
                    changed = True
    for section in ("llm_profiles", "image_profiles", "cheap_llm_profiles", "dialogue_llm_profiles"):
        if section in config:
            for profile_name, profile in config[section].items():
                for key_name in ("api_key",):
                    val = profile.get(key_name, "")
                    if val and not val.startswith("enc:"):
                        profile[key_name] = "enc:" + encrypt_value(val)
                        changed = True
    return changed


def encrypt_config_keys(config_path: str | Path):
    config_path = Path(config_path)
    if not config_path.exists():
        return
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if _encrypt_config_dict(config):
        # [Bug M3] 使用原子写入，防止写入中断损坏配置文件
        atomic_write_json(config_path, config, backup=True)
        logger.info("Encrypted API keys in %s", config_path)


def decrypt_config_keys(config: dict) -> dict:
    config = dict(config)
    for section in ("llm", "image", "embedding", "cheap_llm", "dialogue_llm"):
        if section in config:
            for key_name in ("api_key",):
                val = config[section].get(key_name, "")
                if val.startswith("enc:"):
                    config[section][key_name] = decrypt_value(val[4:])
    for section in ("llm_profiles", "image_profiles", "cheap_llm_profiles", "dialogue_llm_profiles"):
        if section in config:
            for profile_name, profile in config[section].items():
                for key_name in ("api_key",):
                    val = profile.get(key_name, "")
                    if val.startswith("enc:"):
                        profile[key_name] = decrypt_value(val[4:])
    return config
