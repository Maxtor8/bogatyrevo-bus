@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul

set "PY_CMD="
py -3 --version >nul 2>nul && set "PY_CMD=py -3"
if not defined PY_CMD (
    python --version >nul 2>nul && set "PY_CMD=python"
)
if not defined PY_CMD (
    echo [ERROR] Python 3 not found!
    pause
    exit /b 1
)

%PY_CMD% update_schedule.py
