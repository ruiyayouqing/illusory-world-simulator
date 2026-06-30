"""
太虚幻境 - 虚拟世界人生模拟器
一键打包脚本（绿色免安装版）

使用方法：
    python build_package.py

输出：
    dist/太虚幻境_vX.X/
    dist/太虚幻境_vX.X.zip
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

VERSION = "1.0"

BASE_DIR = Path(__file__).parent.resolve()
DIST_DIR = BASE_DIR / "dist"
PACKAGE_NAME = f"太虚幻境_v{VERSION}"
PACKAGE_DIR = DIST_DIR / PACKAGE_NAME
PYTHON_DIR = PACKAGE_DIR / "python"
APP_DIR = PACKAGE_DIR / "app"

VENV_DIR = BASE_DIR / "venv"
PYTHON_SOURCE_DIR = Path(sys.base_prefix).resolve()

APP_FILES_TO_COPY = [
    "server.py",
    "index.html",
    "config.json.example",
    ".env.example",
    "README.md",
    "白皮书.md",
    "modules",
    "routes",
    "frontend",
    "plugins",
    "data",
    "static",
    "saves",
    "requirements.txt",
]

APP_DIRS_EXCLUDE = [
    "__pycache__",
    ".git",
    ".pytest_cache",
    "tests",
    "venv",
    "dist",
    "build",
    "node_modules",
]

APP_FILES_EXCLUDE = [
    "*.pyc",
    "*.pyo",
    "*.bak",
    "*.log",
    "*.egg-info",
    ".access_token",
    ".secret_key",
    ".test_world_id",
    "test_*.py",
    "test_*.png",
    "recon_*.png",
    "fix_slots.py",
    "test_load.json",
]


def log(msg: str):
    print(f"  {msg}")


def robocopy(src: Path, dst: Path, args: list[str] | None = None):
    """使用 robocopy 复制目录（Windows 自带，速度快）"""
    cmd = ["robocopy", str(src), str(dst), "/E", "/MT:8", "/R:2", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS"]
    if args:
        cmd.extend(args)
    
    result = subprocess.run(cmd, capture_output=False)
    # robocopy 返回码 0-7 都是成功
    if result.returncode >= 8:
        raise RuntimeError(f"robocopy 失败，返回码: {result.returncode}")


def copy_python_env():
    """复制完整的 Python 环境 + venv 中的依赖包"""
    log(f"Python 源: {PYTHON_SOURCE_DIR}")
    log(f"venv 源: {VENV_DIR}")
    
    if PYTHON_DIR.exists():
        shutil.rmtree(PYTHON_DIR)
    PYTHON_DIR.mkdir(parents=True, exist_ok=True)
    
    # 1. 复制完整的 Python 解释器
    log("复制 Python 解释器...")
    robocopy(PYTHON_SOURCE_DIR, PYTHON_DIR, [
        "/XD", "tcl", "Doc", "include", "libs",
        "/XF", "*.pdb", "*.lib",
    ])
    
    # 2. 合并 venv 的 site-packages 到 Python 的 site-packages
    log("合并依赖包...")
    venv_site_packages = VENV_DIR / "Lib" / "site-packages"
    py_site_packages = PYTHON_DIR / "Lib" / "site-packages"
    
    if venv_site_packages.exists():
        robocopy(venv_site_packages, py_site_packages, [
            "/XD", "__pycache__", "pip", "setuptools", "wheel",
            "/XF", "*.pyc", "*.pyo",
        ])
    
    # 3. 复制 venv 的 Scripts（如 uvicorn 等命令行工具）
    log("复制命令行工具...")
    venv_scripts = VENV_DIR / "Scripts"
    py_scripts = PYTHON_DIR / "Scripts"
    
    if venv_scripts.exists():
        # 只复制非 python/pip 的 exe 和脚本
        for item in venv_scripts.iterdir():
            name = item.name.lower()
            if name.startswith("python") or name.startswith("pip"):
                continue
            if name.startswith("activate") or name.startswith("deactivate"):
                continue
            if name == ".empty":
                continue
            dst = py_scripts / item.name
            if not dst.exists():
                if item.is_dir():
                    shutil.copytree(item, dst)
                else:
                    shutil.copy2(item, dst)
    
    log("Python 环境复制完成")


def copy_app_files():
    """复制应用程序文件"""
    log(f"复制程序文件 -> {APP_DIR}")
    
    if APP_DIR.exists():
        shutil.rmtree(APP_DIR)
    APP_DIR.mkdir(parents=True, exist_ok=True)
    
    for item_name in APP_FILES_TO_COPY:
        src = BASE_DIR / item_name
        if not src.exists():
            log(f"  跳过不存在的: {item_name}")
            continue
        
        dst = APP_DIR / item_name
        if src.is_file():
            shutil.copy2(src, dst)
            # config.json.example -> config.json
            if item_name == "config.json.example":
                final_dst = APP_DIR / "config.json"
                if final_dst.exists():
                    final_dst.unlink()
                dst.rename(final_dst)
                log(f"  复制: config.json")
            else:
                log(f"  复制: {item_name}")
        else:
            # 目录：用 robocopy 复制
            exclude_dirs = [d for d in APP_DIRS_EXCLUDE if (src / d).exists()]
            args = ["/XF"] + APP_FILES_EXCLUDE
            if exclude_dirs:
                args.extend(["/XD"] + exclude_dirs)
            
            robocopy(src, dst, args)
            log(f"  复制: {item_name}/")
    
    log("程序文件复制完成")


def copy_launcher():
    """复制启动脚本和说明"""
    log("复制启动脚本和说明文档")
    
    # 用英文脚本，避免编码问题
    bat_content = r"""@echo off
title TaiXuHuanJing
chcp 65001 >nul 2>&1
cd /d "%~dp0"

set "PYTHON_DIR=%~dp0python"
set "APP_DIR=%~dp0app"

echo ===========================================
echo    Tai Xu Huan Jing - Virtual Life Sim
echo ===========================================
echo.

if not exist "%PYTHON_DIR%\python.exe" (
    echo [ERROR] Python not found!
    echo Expected: %PYTHON_DIR%\python.exe
    echo.
    pause
    exit /b 1
)

if not exist "%APP_DIR%\server.py" (
    echo [ERROR] App files not found!
    echo Expected: %APP_DIR%\server.py
    echo.
    pause
    exit /b 1
)

echo [OK] Python: %PYTHON_DIR%\python.exe
echo [OK] App: %APP_DIR%
echo.

set "PATH=%PYTHON_DIR%;%PYTHON_DIR%\Scripts;%PATH%"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONNOUSERSITE=1"

echo Starting server...
echo.
echo After server starts, browser will open automatically.
echo If not, please visit: http://127.0.0.1:8004
echo Press Ctrl+C to stop.
echo ===========================================
echo.

cd /d "%APP_DIR%"
"%PYTHON_DIR%\python.exe" server.py

echo.
echo Server stopped.
pause
"""
    # 用 GBK 编码写入，避免 cmd 乱码
    (PACKAGE_DIR / "启动.bat").write_text(bat_content, encoding="gbk")
    
    readme_content = f"""太虚幻境 - 虚拟世界人生模拟器 v{VERSION}
{'='*40}

【快速开始】
1. 双击「启动.bat」
2. 等待服务启动（会自动打开浏览器）
3. 开始游戏！

【系统要求】
- Windows 7 / 8 / 10 / 11
- 至少 2GB 内存
- 需要联网（使用 AI 大模型 API）

【注意事项】
- 请勿删除程序目录中的任何文件
- 游戏存档保存在 app\\saves\\ 目录下
- 配置文件为 app\\config.json
- 关闭时，请先在控制台按 Ctrl+C，或直接关闭窗口

【首次使用】
1. 启动后，打开设置页面（⚙️按钮）
2. 配置你的 AI API Key 和 Base URL
3. 保存设置后即可开始游戏

【问题反馈】
如遇到问题，请查看 app\\server.log 日志文件
"""
    (PACKAGE_DIR / "使用说明.txt").write_text(readme_content, encoding="utf-8")
    log("启动脚本复制完成")


def optimize_python_env():
    """精简 Python 环境，删除不需要的文件"""
    log("正在精简 Python 环境...")
    
    removed_count = 0
    
    # 删除 __pycache__ 目录
    for pycache in list(PYTHON_DIR.rglob("__pycache__")):
        if pycache.is_dir():
            shutil.rmtree(pycache, ignore_errors=True)
            removed_count += 1
    
    # 删除 .pyc 和 .pyo 文件
    for pat in ["*.pyc", "*.pyo"]:
        for f in list(PYTHON_DIR.rglob(pat)):
            try:
                f.unlink()
                removed_count += 1
            except:
                pass
    
    # 删除 tkinter/tcl（我们不需要 GUI）
    for d in ["tkinter", "turtledemo", "idlelib", "unittest"]:
        p = PYTHON_DIR / "Lib" / d
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    
    tcl_dir = PYTHON_DIR / "tcl"
    if tcl_dir.exists():
        shutil.rmtree(tcl_dir, ignore_errors=True)
    
    # 复制 sqlite3.dll 到 Python 根目录（ChromaDB 需要）
    sqlite_dll = PYTHON_DIR / "DLLs" / "sqlite3.dll"
    if sqlite_dll.exists():
        shutil.copy2(sqlite_dll, PYTHON_DIR / "sqlite3.dll")
        log("  复制 sqlite3.dll 到根目录")
    
    # 删除 .dist-info（可选，节省空间）
    # for di in (PYTHON_DIR / "Lib" / "site-packages").glob("*.dist-info"):
    #     if di.is_dir():
    #         shutil.rmtree(di, ignore_errors=True)
    
    log(f"精简完成（清理了 {removed_count} 个缓存文件）")


def create_zip():
    """创建压缩包"""
    zip_path = DIST_DIR / f"{PACKAGE_NAME}.zip"
    log(f"正在创建压缩包: {zip_path.name}")
    log("  (这可能需要几分钟...)")
    
    if zip_path.exists():
        zip_path.unlink()
    
    total = 0
    done = 0
    for root, dirs, files in os.walk(PACKAGE_DIR):
        total += len(files)
    
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as zf:
        for root, dirs, files in os.walk(PACKAGE_DIR):
            for file in files:
                file_path = Path(root) / file
                arcname = file_path.relative_to(DIST_DIR)
                zf.write(file_path, arcname)
                done += 1
                if done % 500 == 0:
                    log(f"  已压缩 {done}/{total} 文件...")
    
    size_mb = zip_path.stat().st_size / (1024 * 1024)
    log(f"压缩包创建完成: {size_mb:.1f} MB")


def main():
    print()
    print("╔══════════════════════════════════════════╗")
    print("║   太虚幻境 - 一键打包脚本             ║")
    print("╚══════════════════════════════════════════╝")
    print()
    
    if not VENV_DIR.exists():
        print(f"[错误] 找不到虚拟环境: {VENV_DIR}")
        print("请先创建虚拟环境并安装依赖")
        sys.exit(1)
    
    # 清理旧的打包产物
    if PACKAGE_DIR.exists():
        log("清理旧的打包产物...")
        shutil.rmtree(PACKAGE_DIR)
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    
    print()
    print("【1/5】复制 Python 环境")
    print("─" * 40)
    copy_python_env()
    
    print()
    print("【2/5】复制程序文件")
    print("─" * 40)
    copy_app_files()
    
    print()
    print("【3/5】复制启动脚本")
    print("─" * 40)
    copy_launcher()
    
    print()
    print("【4/5】精简 Python 环境")
    print("─" * 40)
    optimize_python_env()
    
    print()
    print("【5/5】创建压缩包")
    print("─" * 40)
    create_zip()
    
    # 统计目录大小
    total_size = 0
    for f in PACKAGE_DIR.rglob("*"):
        if f.is_file():
            total_size += f.stat().st_size
    size_mb = total_size / (1024 * 1024)
    
    print()
    print("╔══════════════════════════════════════════╗")
    print("║             打包完成！                 ║")
    print("╠══════════════════════════════════════════╣")
    print(f"║  输出目录: dist\\{PACKAGE_NAME}")
    print(f"║  目录大小: {size_mb:.1f} MB")
    print(f"║  压缩包:   dist\\{PACKAGE_NAME}.zip")
    print("╚══════════════════════════════════════════╝")
    print()
    print("使用方法: 解压后双击「启动.bat」")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n用户取消")
        sys.exit(1)
    except Exception as e:
        print(f"\n[错误] 打包失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
