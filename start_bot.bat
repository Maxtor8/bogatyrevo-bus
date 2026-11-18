@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "bot_config.json" (
    echo [ОШИБКА] Не найден файл bot_config.json!
    echo Скопируйте bot_config.example.json в bot_config.json и впишите токен от @BotFather.
    pause
    exit /b 1
)

:loop
echo [%date% %time%] Запуск Telegram-бота...
python bot.py
if errorlevel 1 (
    py -3 bot.py
)
echo [%date% %time%] Бот завершился, перезапуск через 5 секунд...
timeout /t 5 /nobreak >nul
goto loop
