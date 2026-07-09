@echo off
title AEL Gateway - Aelvoxim Desktop Gateway
cd /d "C:\Aelvoxim\aelvoxim-gateway"
echo [AEL Gateway] 正在启动...
echo.
python -c "import uvicorn; from gateway.server import app; uvicorn.run(app, host='0.0.0.0', port=9705, log_level='info')"
if errorlevel 1 (
    echo [AEL Gateway] 启动失败！错误代码: %errorlevel%
    pause
)
pause
