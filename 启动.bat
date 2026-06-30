@echo off
chcp 65001 >nul 2>&1
title 太虚幻境 - 虚拟世界人生模拟器

cd /d "%~dp0"

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║                                          ║
echo  ║     太虚幻境 - 虚拟世界人生模拟器      ║
echo  ║                                          ║
echo  ╚══════════════════════════════════════════╝
echo.

set "PYTHON_DIR=%~dp0python"
set "APP_DIR=%~dp0app"

echo  [检查] 验证文件完整性...

if not exist "%PYTHON_DIR%\python.exe" (
    echo  [错误] 找不到 Python 环境！
    echo  预期路径: %PYTHON_DIR%\python.exe
    echo.
    pause
    exit /b 1
)

if not exist "%APP_DIR%\server.py" (
    echo  [错误] 找不到程序文件！
    echo  预期路径: %APP_DIR%\server.py
    echo.
    pause
    exit /b 1
)

echo  [OK] Python 环境: %PYTHON_DIR%\python.exe
echo  [OK] 程序目录: %APP_DIR%

echo.
echo  [启动] 正在初始化环境...
set "PATH=%PYTHON_DIR%;%PYTHON_DIR%\Scripts;%PATH%"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONNOUSERSITE=1"

echo  [启动] Python 版本检查...
"%PYTHON_DIR%\python.exe" --version
if errorlevel 1 (
    echo  [错误] Python 无法启动！
    echo  可能原因: 缺少 Visual C++ 运行库
    echo  请下载安装: https://aka.ms/vs/17/release/vc_redist.x64.exe
    echo.
    pause
    exit /b 1
)

echo.
echo  [启动] 正在启动服务...
echo.
echo  ═══════════════════════════════════════════
echo  服务启动后会自动打开浏览器
echo  如果没有自动打开，请手动访问：http://127.0.0.1:8004
echo  按 Ctrl+C 可以停止服务
echo  ═══════════════════════════════════════════
echo.

cd /d "%APP_DIR%"
"%PYTHON_DIR%\python.exe" server.py

echo.
echo.
echo  服务已停止。
pause
