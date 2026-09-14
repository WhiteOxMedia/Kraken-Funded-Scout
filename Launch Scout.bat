@echo off
title Kraken Scout - Scanner + Dashboard
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Setting up venv...
    uv sync
) else (
    ".venv\Scripts\python.exe" --version >nul 2>&1
    if errorlevel 1 (
        echo Venv is broken - rebuilding...
        rmdir /s /q .venv 2>nul
        uv sync
    )
)
if exist "__pycache__" rmdir /s /q "__pycache__"
if exist "trend\__pycache__" rmdir /s /q "trend\__pycache__"

REM Check if already running - don't start duplicate scanner
REM (PowerShell checks command lines; tasklist/wmic do not work reliably)
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object {$_.CommandLine -like '*background_scanner*'}) { exit 0 } else { exit 1 }" >nul 2>&1
if errorlevel 1 (
    REM Scanner NOT running - spawn it detached (hidden, UTF-8 logs in
    REM logs\, stale-pid retake) and wait for the verified boot
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_detached.ps1" -Component background_scanner -LogStem scanner -Base "%~dp0." -RetakeStalePid
) else (
    echo Scanner is already running - skipping scanner start.
)

ping -n 3 127.0.0.1 >nul

REM If the console dashboard is already serving, just open the browser and exit
netstat -an | findstr ":8501" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo Console already running - opening browser...
    start "" "http://localhost:8501"
    ping -n 3 127.0.0.1 >nul
    exit /b
)

REM Start dashboard in this window - opens the browser automatically
echo Starting console on http://localhost:8501...
".venv\Scripts\streamlit.exe" run app.py --server.port 8501 --server.runOnSave false

echo.
echo Dashboard stopped. Scanner is in a separate window.
pause
