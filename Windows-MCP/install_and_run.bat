@echo off
REM ===================================================
REM Windows-MCP 一键安装启动脚本
REM 在 Windows 本机 (CMD 管理员模式) 运行
REM ===================================================
title Aelvoxim Windows-MCP 安装启动
cd /d %~dp0

echo ========================================
echo    Aelvoxim Windows-MCP 一键安装
echo ========================================
echo.
echo 此脚本将在 Windows 本机安装并启动 Windows-MCP，
echo 让 AI 大脑可以通过 MCP 协议操作你的桌面。
echo.

REM 检查是否管理员
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [警告] 建议以管理员身份运行（右键 → 以管理员身份运行）
    echo        某些功能（如 UIA 自动化）需要管理员权限
    echo.
)

REM 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 找不到 Python
    echo   请先安装 Python 3.12+ https://www.python.org/downloads/
    echo   安装时勾选 "Add Python to PATH"
    pause
    exit /b 1
)
echo [OK] Python: 
python --version

REM 安装 uv（快速包管理器）
where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [1/3] 安装 uv 包管理器...
    pip install uv -q
) else (
    echo [OK] uv 已安装
)

echo.
echo [2/3] 拉取 Windows-MCP 最新版本...
uv tool install windows-mcp -q 2>nul
if %errorlevel% neq 0 (
    uvx windows-mcp --version >nul 2>&1
)
echo [OK] Windows-MCP 就绪

echo.
echo [3/3] 启动 Windows-MCP 服务...
echo.
echo ========================================
echo  服务地址: http://127.0.0.1:8000
echo  按 Ctrl+C 停止服务
echo ========================================
echo.

uvx windows-mcp serve --transport streamable-http --host 127.0.0.1 --port 8000

pause
