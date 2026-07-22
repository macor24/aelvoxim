@echo off
REM ===================================================
REM Windows-MCP 测试启动脚本
REM 在 Windows 本机 (CMD 或 PowerShell) 运行
REM ===================================================

echo ===== Windows-MCP 测试 =====
echo.

REM 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 找不到 Python，请先安装 Python 3.12+
    pause
    exit /b 1
)

REM Step 1: 安装 uv（如果没装）
where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo [1/3] 安装 uv 包管理器...
    pip install uv
) else (
    echo [1/3] uv 已安装
)

REM Step 2: 启动 Windows-MCP（streamable-http 模式）
echo [2/3] 启动 Windows-MCP...
echo.
echo 启动后 Windows-MCP 会在 http://127.0.0.1:8000 监听
echo 按 Ctrl+C 停止
echo.

uvx windows-mcp serve --transport streamable-http --host 127.0.0.1 --port 8000
