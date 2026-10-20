@echo off
title Monero CPU Miner - XMR to Kraken
cd /d "%~dp0"

echo ============================================
echo   Monero CPU Miner - Starting...
echo ============================================
echo.

REM Run the miner manager
.venv\Scripts\python.exe miner.py %*

pause
