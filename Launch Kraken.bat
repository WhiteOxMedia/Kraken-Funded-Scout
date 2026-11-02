@echo off
title Kraken - Full Stack Launcher
cd /d "%~dp0"

REM Launch Kraken - starts ONLY what is not already running. Never kills.
REM All detection logic lives in launch_stack.ps1 (read-only checks).
REM The scanner and bot also carry their own PID locks as the final backstop.

if not exist ".venv\Scripts\python.exe" (
    echo Setting up venv...
    set "UV_LINK_MODE=copy"
    uv sync
) else (
    ".venv\Scripts\python.exe" --version >nul 2>&1
    if errorlevel 1 (
        echo Venv is broken - rebuilding...
        rmdir /s /q .venv 2>nul
        set "UV_LINK_MODE=copy"
        uv sync
    )
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch_stack.ps1"
exit /b 0
