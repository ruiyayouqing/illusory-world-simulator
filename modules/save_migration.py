"""[v1.4 P2-9] 存档版本迁移框架

设计目标：
  - 链式迁移：0.1.0 → 0.2.0 → 0.3.0 ... 每步只升一个小版本
  - 注册表模式：新增迁移只需 register 一条 (from, to, migrator)
  - 安全回滚：迁移前自动备份 world_dir 到 backups/
  - 失败停止：单步失败抛异常，manifest.version 保持旧值，下次启动可重试
  - 版本比较：用元组 (major, minor, patch) 而非字符串，避免 "0.10.0" < "0.2.0" 问题

迁移函数签名：
    def migrator(world_dir: Path, save_manager) -> None
        - world_dir: 存档目录 (e.g. saves/custom_xxxx/)
        - save_manager: SaveManager 实例，可调用 _read_json/_write_json 复用 IO 工具
        - 失败时抛任意异常，迁移框架会捕获并记录
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("chronoverse.migration")


# 当前存档格式版本（字符串形式，写入 manifest.version）
CURRENT_VERSION = "0.2.0"


# ----------------------------------------------------------------------------
# 版本号工具
# ----------------------------------------------------------------------------

def parse_version(v: str) -> tuple[int, ...]:
    """把 '0.2.0' 解析为 (0, 2, 0)；非法格式返回 (0, 0, 0)。

    用元组比较可避免字符串比较的 '0.10.0' < '0.2.0' 陷阱。
    """
    if not v:
        return (0, 0, 0)
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            # 非数字段（如 '0.2.0-rc1'）按 0 处理，保证向后兼容
            parts.append(0)
    return tuple(parts) if parts else (0, 0, 0)


def version_lt(a: str, b: str) -> bool:
    """a < b ?"""
    return parse_version(a) < parse_version(b)


def version_eq(a: str, b: str) -> bool:
    """a == b ?"""
    return parse_version(a) == parse_version(b)


# ----------------------------------------------------------------------------
# 迁移注册表
# ----------------------------------------------------------------------------

# 每条记录: (from_version, to_version, migrator_fn)
MigrationFn = Callable[[Path, object], None]
_MIGRATIONS: list[tuple[str, str, MigrationFn]] = []


def register(from_version: str, to_version: str) -> Callable[[MigrationFn], MigrationFn]:
    """装饰器：注册一个迁移函数。

    用法：
        @register("0.1.0", "0.2.0")
        def migrate_0_1_to_0_2(world_dir, sm):
            ...
    """
    def decorator(fn: MigrationFn) -> MigrationFn:
        _MIGRATIONS.append((from_version, to_version, fn))
        # 按from版本升序排列，保证链式迁移按顺序执行
        _MIGRATIONS.sort(key=lambda m: parse_version(m[0]))
        return fn
    return decorator


def list_migrations() -> list[tuple[str, str, str]]:
    """列出所有已注册迁移（用于诊断/日志）"""
    return [(f, t, fn.__name__) for f, t, fn in _MIGRATIONS]


def find_chain(from_version: str, to_version: str) -> list[tuple[str, str, MigrationFn]]:
    """找出从 from_version 到 to_version 的迁移链。

    返回按顺序执行的迁移列表。如果找不到完整链，返回部分链并让调用方决定。
    """
    chain: list[tuple[str, str, MigrationFn]] = []
    current = from_version
    # 防御性循环上限：版本号通常 <100 个
    for _ in range(100):
        if version_eq(current, to_version):
            break
        # 找到从 current 出发的下一条迁移
        next_step: Optional[tuple[str, str, MigrationFn]] = None
        for f, t, fn in _MIGRATIONS:
            if version_eq(f, current):
                # 优先选择 to_version 不超过目标版本的迁移（避免越界）
                if not version_lt(t, current) and not version_lt(to_version, t):
                    next_step = (f, t, fn)
                    break
        if not next_step:
            # 找不到下一步，链断开
            logger.warning("迁移链断裂: %s → %s，剩余未迁移", current, to_version)
            break
        chain.append(next_step)
        current = next_step[1]
    return chain


# ----------------------------------------------------------------------------
# 内置迁移：0.1.0 → 0.2.0
# ----------------------------------------------------------------------------

@register("0.1.0", "0.2.0")
def _migrate_0_1_to_0_2(world_dir: Path, save_manager) -> None:
    """0.1.0 → 0.2.0: NPC schema 增加了 role/role_history/relation_history 字段

    - 给现有 NPC 补充初始 role（从 tags 推断）
    - 补充 role_history / relation_history 默认空列表
    - 更新 manifest.version
    """
    npcs_dir = world_dir / "state" / "npcs"
    if npcs_dir.exists():
        for npc_file in npcs_dir.glob("*.json"):
            npc_data = save_manager._read_json(npc_file)
            if not npc_data:
                continue
            if not npc_data.get("role"):
                # 从 tags 推断第一个非性格标签作为初始身份
                for tag in npc_data.get("tags", []):
                    if tag not in ["善良", "豪爽", "谨慎", "勇敢", "胆小",
                                   "聪明", "憨厚", "普通人", "穿越者",
                                   "转世者", "前世记忆"]:
                        npc_data["role"] = tag
                        break
                if not npc_data.get("role"):
                    npc_data["role"] = ""
            npc_data.setdefault("role_history", [])
            npc_data.setdefault("relation_history", [])
            save_manager._write_json(npc_file, npc_data)

    # 更新 manifest 版本号（链式迁移最后一步统一更新，但保留以兼容旧测试）
    manifest_path = world_dir / "manifest.json"
    manifest = save_manager._read_json(manifest_path)
    manifest["version"] = "0.2.0"
    save_manager._write_json(manifest_path, manifest)


# ----------------------------------------------------------------------------
# 迁移执行器
# ----------------------------------------------------------------------------

def migrate_save(world_id: str, from_version: str, world_dir: Path,
                 save_manager, target_version: str = CURRENT_VERSION) -> str:
    """执行存档迁移，返回最终版本号。

    流程：
      1. 计算迁移链
      2. 如果链非空，先备份 world_dir 到 backups/pre_migrate_{from}_{timestamp}/
      3. 依次执行每个迁移函数；失败抛异常，已成功的步骤不会回滚但 manifest 仍是上一步版本
      4. 全部成功后 manifest.version = target_version
    """
    if version_eq(from_version, target_version):
        logger.info("存档 %s 版本 %s 已是目标版本，无需迁移", world_id, from_version)
        return from_version

    chain = find_chain(from_version, target_version)
    if not chain:
        logger.warning("存档 %s 无迁移路径 %s → %s", world_id, from_version, target_version)
        # 不报错，让加载继续（可能旧版本数据结构刚好兼容）
        return from_version

    logger.info("存档 %s 迁移链: %s",
                world_id,
                " → ".join([f"{f}→{t}" for f, t, _ in chain]))

    # 备份（仅一次，覆盖整条链）
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = world_dir / "backups" / f"pre_migrate_{parse_version(from_version)}_{timestamp}"
    try:
        # 排除 backups 子目录本身，避免递归复制
        backup_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            world_dir, backup_dir,
            ignore=shutil.ignore_patterns("backups"),
        )
        logger.info("已备份存档 %s 至 %s", world_id, backup_dir)
    except Exception as e:
        logger.warning("备份失败（继续迁移）: %s", e)

    # 依次执行迁移
    current = from_version
    for from_v, to_v, fn in chain:
        logger.info("执行迁移 %s → %s (%s)", from_v, to_v, fn.__name__)
        try:
            fn(world_dir, save_manager)
            current = to_v
            # 每步成功后立即更新 manifest.version，便于中断后断点续传
            manifest_path = world_dir / "manifest.json"
            manifest = save_manager._read_json(manifest_path)
            manifest["version"] = to_v
            save_manager._write_json(manifest_path, manifest)
            logger.info("  ✅ %s → %s 完成", from_v, to_v)
        except Exception as e:
            logger.error("  ❌ 迁移 %s → %s 失败: %s", from_v, to_v, e, exc_info=True)
            # 抛出，让调用方知道加载未完成；用户可从备份恢复
            raise RuntimeError(f"存档迁移失败 {from_v}→{to_v}: {e}") from e

    return current
