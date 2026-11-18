@echo off
chcp 65001 >nul
cd /d "%~dp0"

python update_schedule.py
if errorlevel 1 (
    py -3 update_schedule.py
)
