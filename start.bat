@echo off
cd /d "%~dp0"
title 太虚幻境 - 虚拟世界人生模拟器

echo  ========================================
echo    太虚幻境 - 虚拟世界人生模拟器
echo  ========================================
echo.

if not exist "venv\Scripts\python.exe" goto setup
goto start

:setup
echo  [1/2] Creating virtual environment...
python -m venv venv
echo  [2/2] Installing dependencies...
"venv\Scripts\pip.exe" install -r requirements.txt
echo.

:start
echo  Starting server...
echo.
echo  URL: http://127.0.0.1:8004
echo  Press Ctrl+C to stop
echo.

start http://127.0.0.1:8004

"venv\Scripts\python.exe" server.py

echo.
echo  Server stopped.
echo.
pause
