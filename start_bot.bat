@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul

if not exist "bot_config.json" (
    echo [ERROR] bot_config.json not found!
    echo Copy bot_config.example.json to bot_config.json and paste your @BotFather token.
    pause
    exit /b 1
)

set "PY_CMD="
py -3 --version >nul 2>nul && set "PY_CMD=py -3"
if not defined PY_CMD (
    python --version >nul 2>nul && set "PY_CMD=python"
)
if not defined PY_CMD (
    echo [ERROR] Python 3 not found!
    echo Install Python 3 from python.org and check "Add python.exe to PATH".
    pause
    exit /b 1
)

:loop
echo [%date% %time%] Starting Telegram bot...
%PY_CMD% bot.py
echo [%date% %time%] Bot exited, restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop
