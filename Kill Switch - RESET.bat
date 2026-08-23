@echo off
title Kraken Scout - Kill Switch RESET
cd /d "%~dp0"

if not exist "kill_switch.lock" (
    echo ================================================================
    echo   Kill switch is NOT active
    echo   Trading is running normally.
    echo ================================================================
    echo.
    pause
    exit /b
)

echo ================================================================
echo   KILL SWITCH - RESET (RESUME TRADING)
echo ================================================================
echo.
echo   This will:
echo     1. Delete the kill switch lock file
echo     2. Resume normal trading (scanner + bot)
echo.
echo   Press any key to RESET, or close this window to cancel.
pause >nul

echo.
echo   Resetting kill switch...
set "PY=.venv\Scripts\python.exe"
%PY% --version >nul 2>&1
if errorlevel 1 set "PY=C:\Python314\python.exe"
%PY% -c "from signal_bridge import reset_kill_switch; reset_kill_switch(); print('Kill switch reset - trading resumed')"

echo.
echo ================================================================
echo   Kill switch reset. Trading resumes on next scan cycle.
echo ================================================================
echo.
pause
