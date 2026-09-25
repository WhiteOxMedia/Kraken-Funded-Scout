@echo off
title Kraken Scout Setup
cd /d "%~dp0"

REM The whole flow in one click: preflight uv, stage Scout + the monero
REM payload, build the venv, then the setup window opens - the Miner Bot
REM checkbox is the LAST step (default ON; unchecking it installs and
REM registers nothing).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if errorlevel 1 (
    echo.
    echo Setup hit an error - the window above says what failed.
    pause
)
