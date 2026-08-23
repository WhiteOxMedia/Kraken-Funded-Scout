@echo off
title Kraken - Full Stack Launcher
cd /d "%~dp0"

echo ============================================================
echo   KRAKEN - launching full stack
echo   1. Volatility Engine   (thresholds from real data)
echo   2. Scout Scanner       (rotation-aware auto-scanner)
echo   3. Trader Bot          (executes bridge signals)
echo   4. Console Dashboard   (localhost:8501)
echo ============================================================

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

REM -- 1. Volatility Engine (own window, dup-safe) ----------------------
REM (PowerShell checks command lines; tasklist/wmic do not work reliably)
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object {$_.CommandLine -like '*volatility_engine*'}) { exit 0 } else { exit 1 }" >nul 2>&1
if errorlevel 1 (
    start "Volatility Engine" cmd /c ".venv\Scripts\python.exe -X utf8 volatility_engine.py 2>&1 & pause"
    echo Volatility Engine started.
) else (
    echo Volatility Engine already running - skipping.
)

REM -- 2. Scanner (own window, dup-safe) --------------------------------
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object {$_.CommandLine -like '*background_scanner*'}) { exit 0 } else { exit 1 }" >nul 2>&1
if errorlevel 1 (
    start "Scout Scanner" cmd /c ".venv\Scripts\python.exe background_scanner.py 2>&1 & pause"
    echo Scanner started.
) else (
    echo Scanner already running - skipping.
)

REM -- 3. Trader bot (own window, dup-safe, uses the BOT venv) ----------
set BOT_DIR=C:\Users\birds\Workspace\kraken_xrp_trading_bot
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object {$_.CommandLine -like '*bot_runner*'}) { exit 0 } else { exit 1 }" >nul 2>&1
if errorlevel 1 (
    start "Kraken Trader" cmd /c "cd /d %BOT_DIR% && .venv\Scripts\python.exe bot_runner.py --interval 15 2>&1 & pause"
    echo Trader bot started.
) else (
    echo Trader bot already running - skipping.
)

REM -- 4. Console dashboard (this window; open browser if already up) ---
ping -n 4 127.0.0.1 >nul
netstat -an | findstr ":8501" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo Console already running - opening browser...
    start "" "http://localhost:8501"
    ping -n 3 127.0.0.1 >nul
    exit /b
)

echo Starting console on http://localhost:8501...
".venv\Scripts\streamlit.exe" run app.py --server.port 8501 --server.runOnSave false

echo.
echo Dashboard stopped. Scanner / Volatility Engine / Trader run in separate windows.
pause
