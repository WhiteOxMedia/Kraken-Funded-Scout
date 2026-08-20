@echo off
title Kraken Scout - Scanner + Dashboard
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Setting up venv...
    uv sync
)
if exist "__pycache__" rmdir /s /q "__pycache__"
if exist "trend\__pycache__" rmdir /s /q "trend\__pycache__"

REM Check if already running - don't start duplicates
tasklist /fi "imagename eq python.exe" /fo csv 2>nul | findstr "background_scanner" >nul
if not errorlevel 1 (
    echo Scanner is already running. Close the scanner window first.
    pause
    exit /b
)

echo ================================================================
echo   Kraken Scout - Scanner + Dashboard
echo   Scanner runs in background. Dashboard opens in browser.
echo   Close this window to stop everything.
echo ================================================================
echo.

REM Start background scanner in a new window
start "Scout Scanner" cmd /c ".venv\Scripts\python.exe background_scanner.py 2>&1 & pause"

timeout /t 2 /nobreak >nul

REM Start dashboard in this window
echo Starting dashboard on http://localhost:8501...
".venv\Scripts\streamlit.exe" run app.py --server.port 8501 --server.headless true --server.runOnSave false

echo.
echo Dashboard stopped. Scanner is in a separate window.
pause
