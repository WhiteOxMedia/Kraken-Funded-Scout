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
echo     1. Re-baseline today's balance to the ACTUAL current balance
echo        (the circuit breaker accepts the new low level  -  without this
echo        it re-fires within one bot cycle: 10%% of the OLD morning
echo        balance is gone the moment the flatten landed)
echo     2. Delete the kill switch lock file
echo     3. Resume normal trading (scanner + bot)
echo.
echo   Press any key to RESET, or close this window to cancel.
pause >nul

echo.
echo   Resetting kill switch...
set "PY=.venv\Scripts\python.exe"
%PY% --version >nul 2>&1
if errorlevel 1 set "PY=C:\Python314\python.exe"
%PY% reset_rebaseline.py

echo.
echo ================================================================
if exist "kill_switch.lock" (
    echo   THE RESET DID NOT COMPLETE.
    echo   The kill switch lock is STILL IN PLACE - trading stays paused.
    echo   Read the messages above: either you declined to clear it on a
    echo   stale baseline, or the rebaseline could not run. Re-run this
    echo   shortcut, or run:
    echo     .venv\Scripts\python.exe reset_rebaseline.py ^<balance^>
) else (
    echo   Kill switch reset. Trading resumes on next scan cycle.
    echo   If the balance printed above is wrong, run:
    echo     .venv\Scripts\python.exe reset_rebaseline.py ^<balance^>
)
echo ================================================================
echo.
pause
