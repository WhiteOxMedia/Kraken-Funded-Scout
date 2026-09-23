@echo off
title Kraken - STOP (full stack shutdown)
cd /d "%~dp0"

REM Kraken - STOP - the real OFF switch (Sep 15).
REM Kills BOTH halves of every component pair (the .venv shim + its
REM interpreter child - the "doubles" Task Manager shows), disarms the
REM console watchdog so nothing auto-restarts, and drops stop_stack.lock
REM so the watchdog stays quiet while the fleet is down. The Kraken
REM desktop shortcut starts everything again (and re-arms the watchdog).
REM The Kill Switch bats (trading halt) are separate and untouched.
REM Sep 15: auto-closes on success - the pause only fires on a WARN so a
REM partially-killed stack is actually seen.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_stack.ps1"
if errorlevel 1 (
    echo.
    echo   Some processes survived - read the list above before killing them.
    pause
)
