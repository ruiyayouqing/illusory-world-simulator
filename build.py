#!/usr/bin/env python3
"""
build.py — 前端资源版本号自动管理工具

扫描 app/index.html 中所有 ?v=N 的 JS/CSS 引用，用文件内容的短 hash 替换版本号：
- 文件内容不变 → 版本号不变（浏览器缓存有效）
- 文件内容变化 → 版本号变化（缓存自动失效）

彻底解决「改了 JS/CSS 忘记更新版本号导致浏览器加载旧缓存」的问题。

用法：
  python build.py           # 更新所有版本号（默认）
  python build.py --check   # 只检查需要更新的项，不修改文件（CI/预检用）
  python build.py --help    # 显示帮助

原理：
  index.html 中 <script src="/js/ui.js?v=15"> 的 v=15 是缓存 key。
  build.py 把 v=15 替换为 ui.js 文件内容的 md5 前 8 位（如 v=a3f9c2b1）。
  文件改动后内容 hash 变化，版本号随之变化，浏览器自动丢弃旧缓存。
"""
from __future__ import annotations
import hashlib
import re
import sys
from pathlib import Path

# 项目根目录（build.py 所在目录）
ROOT_DIR = Path(__file__).resolve().parent
APP_DIR = ROOT_DIR / "app"
INDEX_HTML = APP_DIR / "index.html"
# 前端资源本地根目录（web 路径 /js/ /css/ 映射到此）
FRONTEND_DIR = APP_DIR / "frontend"

# 匹配 src="/js/xxx.js?v=N" 或 href="/css/xxx.css?v=N"
# 分组：1=前缀(src=" 或 href=")  2=web路径  3=旧版本号  4=闭合引号
PATTERN = re.compile(
    r'((?:src|href)=["\'])(/?(?:js|css)/[^"\']+\.(?:js|css))\?v=(\w+)(["\'])'
)


def web_to_local(web_path: str) -> Path:
    """把 web 路径 /js/xxx.js 映射到本地 app/frontend/js/xxx.js"""
    rel = web_path.lstrip("/")
    return FRONTEND_DIR / rel


def file_hash(path: Path) -> str:
    """计算文件内容的 md5 前 8 位作为短 hash 版本号"""
    return hashlib.md5(path.read_bytes()).hexdigest()[:8]


def update_versions(check_only: bool = False) -> int:
    """扫描并更新 index.html 中的版本号，返回退出码（0=成功，1=错误）"""
    if not INDEX_HTML.exists():
        print(f"ERROR: 找不到 {INDEX_HTML}")
        return 1
    if not FRONTEND_DIR.exists():
        print(f"ERROR: 找不到前端目录 {FRONTEND_DIR}")
        return 1

    content = INDEX_HTML.read_text(encoding="utf-8")
    changes: list[str] = []
    missing: list[str] = []

    def replacer(m: re.Match) -> str:
        prefix, web_path, old_v, suffix = m.group(1), m.group(2), m.group(3), m.group(4)
        local_path = web_to_local(web_path)
        if not local_path.exists():
            missing.append(web_path)
            return m.group()
        new_v = file_hash(local_path)
        if new_v != old_v:
            changes.append(f"  {web_path}: {old_v} -> {new_v}")
            return f"{prefix}{web_path}?v={new_v}{suffix}"
        return m.group()

    new_content = PATTERN.sub(replacer, content)

    if missing:
        print("WARN: 以下文件在本地找不到，已跳过：")
        for p in missing:
            print(f"  {p}")

    if not changes:
        print("所有版本号已是最新，无需更新。")
        return 0

    print(f"需要更新 {len(changes)} 个版本号：")
    for line in changes:
        print(line)

    if check_only:
        print("\n[CHECK] 仅检查模式，未修改文件。去掉 --check 参数以应用更新。")
        return 0

    INDEX_HTML.write_text(new_content, encoding="utf-8")
    print(f"\n已更新 {len(changes)} 个版本号 → {INDEX_HTML.name}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(__doc__)
        return 0
    check_only = "--check" in args
    return update_versions(check_only=check_only)


if __name__ == "__main__":
    sys.exit(main())
