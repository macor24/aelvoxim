@echo off
chcp 65001 >nul
cd /d C:\Aelvoxim\aelvoxim-gateway
echo ╔══════════════════════════════════════════╗
echo ║     Aelvoxim Desktop Gateway             ║
echo ╚══════════════════════════════════════════╝
echo.
echo Starting on port 9705...

:: Try python first, fallback to py
python main.py 2>nul
if errorlevel 1 (
    py main.py 2>nul
    if errorlevel 1 (
        echo [ERROR] Python not found. Please install Python 3.x
        echo        https://www.python.org/downloads/
        pause
        exit /b 1
    )
)
pause
