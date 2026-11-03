@echo off
title Kraken Scout - Kill Switch (FLATTEN ALL)
cd /d "%~dp0"

if exist "kill_switch.lock" (
    echo ================================================================
    echo   Kill switch is ALREADY ACTIVE
    echo   All trades are blocked. Positions should be flattened.
    echo ================================================================
    echo.
    echo   To turn off: Run 'Kill Switch - RESET.bat'
    echo.
    pause
    exit /b
)

echo ================================================================
echo   KILL SWITCH - FLATTEN ALL POSITIONS
echo ================================================================
echo.
echo   This will:
echo     1. Activate the kill switch (blocks all new trades)
echo     2. Sell ALL positions on Kraken at market
echo     3. Clear the signal bridge database
echo.
echo   The scanner and bot must be running for this to work.
echo   The kill switch stays active until you reset it.
echo.
echo   Press any key to FLATTEN, or close this window to cancel.
pause >nul

echo.
echo   Activating kill switch...
set "PY=.venv\Scripts\python.exe"
%PY% --version >nul 2>&1
if errorlevel 1 set "PY=C:\Python314\python.exe"
%PY% -c "from signal_bridge import trigger_kill_switch; trigger_kill_switch('Manual kill switch - flatten all positions from desktop')"

echo.
echo ================================================================
echo   Kill switch activated. Check scanner/bot logs for sell output.
echo   Run 'Kill Switch - RESET.bat' to turn it off and resume trading.
echo ================================================================
echo.
pause
