@echo off
chcp 65001 >nul
title Aelvoxim Gateway Service Installer
cd /d "%~dp0"

echo ╔══════════════════════════════════════════╗
echo ║   Aelvoxim Gateway Service Installer     ║
echo ╚══════════════════════════════════════════╝
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.10+
    pause
    exit /b 1
)

:: Check if NSSM is available
where nssm >nul 2>&1
if errorlevel 1 (
    echo [INFO] NSSM not found. Downloading...
    powershell -Command "Invoke-WebRequest -Uri 'https://nssm.cc/release/nssm-2.24.zip' -OutFile '%TEMP%\nssm.zip'"
    powershell -Command "Expand-Archive '%TEMP%\nssm.zip' -DestinationPath '%TEMP%\nssm' -Force"
    copy /Y "%TEMP%\nssm\nssm-2.24\win64\nssm.exe" "%~dp0nssm.exe"
    echo [OK] NSSM downloaded to %~dp0nssm.exe
)

set NSSM=%~dp0nssm.exe

:: Stop & remove existing service if any
%NSSM% stop AelvoximGateway >nul 2>&1
%NSSM% remove AelvoximGateway confirm >nul 2>&1

:: Install service
set GATEWAY_DIR=%~dp0
set PYTHON_EXE=python

%NSSM% install AelvoximGateway "%PYTHON_EXE%" "%GATEWAY_DIR%\main.py"
%NSSM% set AelvoximGateway DisplayName "Aelvoxim Desktop Gateway"
%NSSM% set AelvoximGateway Description "Controls Windows desktop apps via HTTP API"
%NSSM% set AelvoximGateway Start SERVICE_AUTO_START
%NSSM% set AelvoximGateway AppDirectory "%GATEWAY_DIR%"
%NSSM% set AelvoximGateway AppStdout "%GATEWAY_DIR%\gateway.log"
%NSSM% set AelvoximGateway AppStderr "%GATEWAY_DIR%\gateway.log"
%NSSM% set AelvoximGateway AppRotateFiles 1
%NSSM% set AelvoximGateway AppRotateSeconds 86400
%NSSM% set AelvoximGateway AppRotateBytes 10485760

:: Start
%NSSM% start AelvoximGateway

echo.
echo [OK] Aelvoxim Gateway service installed and started.
echo      Port: 9705
echo      Log:  gateway.log
echo.
echo To manage:
echo   nssm stop AelvoximGateway     — Stop
echo   nssm start AelvoximGateway    — Start
echo   nssm remove AelvoximGateway   — Uninstall
echo.
pause
