@echo off
title Kraken Scout - Health Check
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
%PY% --version >nul 2>&1
if errorlevel 1 set "PY=C:\Python314\python.exe"

echo ================================================================
echo   SCOUT HEALTH CHECK - one pass, strictly read-only
echo   (verifies scanner, bot, volatility engines, console, and
echo    every ledger's integrity - nothing is restarted or killed)
echo ================================================================
echo.

%PY% -X utf8 health_check.py
set "HC=%errorlevel%"

echo.
if "%HC%"=="0" (
    echo   Verdict: HEALTHY - all clear. Safe to close this window.
) else (
    echo   Verdict: FINDINGS PRESENT - read the [DEAD]/[BAD] lines above.
    echo   This check never fixes anything by itself; see what it named.
)
echo.
pause
