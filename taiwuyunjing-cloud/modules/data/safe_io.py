from __future__ import annotations
import json
import os
import shutil
import tempfile
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("chronoverse.io")

MAX_BACKUPS = 5


def atomic_write_text(file_path: Path, content: str, encoding: str = "utf-8",
                      backup: bool = True) -> bool:
    """
    原子写入文本文件：先写临时文件，fsync后rename，防止写入中断损坏文件
    """
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=str(file_path.parent),
        prefix=f".{file_path.name}.",
        suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, 'w', encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

        if backup and file_path.exists():
            _rotate_backups(file_path)

        os.replace(tmp_name, str(file_path))
        return True
    except Exception as e:
        logger.error("Atomic write failed for %s: %s", file_path, e)
        # [Bug M7] 写入失败时原文件仍完好（os.replace 未成功），只需清理临时文件，
        # 不应恢复备份（否则会用旧备份覆盖完好的原文件）
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        return False


def atomic_write_json(file_path: Path, data: Any, indent: int = 2,
                      ensure_ascii: bool = False, backup: bool = True) -> bool:
    """原子写入JSON文件"""
    content = json.dumps(data, ensure_ascii=ensure_ascii, indent=indent)
    return atomic_write_text(file_path, content, backup=backup)


def load_json_safe(file_path: Path, default: Any = None) -> Any:
    """安全加载JSON，如果主文件损坏尝试从备份恢复"""
    file_path = Path(file_path)
    if not file_path.exists():
        return default

    try:
        content = file_path.read_text(encoding="utf-8")
        return json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        logger.warning("Failed to load %s, trying backups: %s", file_path, e)
        return _restore_latest_backup(file_path, default)


def _rotate_backups(file_path: Path):
    """轮转备份文件: file.bak1 -> file.bak2, file -> file.bak1"""
    for i in range(MAX_BACKUPS - 1, 0, -1):
        src = file_path.with_suffix(f"{file_path.suffix}.bak{i}")
        dst = file_path.with_suffix(f"{file_path.suffix}.bak{i+1}")
        if src.exists():
            try:
                shutil.copy2(str(src), str(dst))
            except OSError:
                pass
    try:
        shutil.copy2(str(file_path), str(file_path.with_suffix(f"{file_path.suffix}.bak1")))
    except OSError:
        pass


def _restore_latest_backup(file_path: Path, default: Any = None) -> Any:
    """从最新的可用备份恢复"""
    for i in range(1, MAX_BACKUPS + 1):
        backup = file_path.with_suffix(f"{file_path.suffix}.bak{i}")
        if backup.exists():
            try:
                content = backup.read_text(encoding="utf-8")
                data = json.loads(content)
                logger.info("Restored %s from backup .bak%d", file_path, i)
                try:
                    shutil.copy2(str(backup), str(file_path))
                except OSError:
                    pass
                return data
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                continue
    logger.warning("No valid backup found for %s", file_path)
    return default
