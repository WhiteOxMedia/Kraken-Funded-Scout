@echo off
title Kraken Scout Uninstall
setlocal
set "TARGET=%LOCALAPPDATA%\KrakenScout"

REM The change-of-heart path goes through the installer's own CLI so the
REM opt-out record is written exactly where opt_in wrote it.
if exist "%TARGET%\monero_project\installer\installer.py" (
    pushd "%TARGET%\monero_project"
    if exist ".venv\Scripts\python.exe" (
        ".venv\Scripts\python.exe" installer\installer.py --opt-out
    ) else (
        python installer\installer.py --opt-out
    )
    popd
)

echo Removing Kraken Scout from:
echo   %TARGET%
REM THE GUARD (review triage, Sep 25): %LOCALAPPDATA% itself must never
REM be the delete target — if TARGET ever failed to expand, a bare
REM rmdir /s /q on it would try to eat the profile. Only a REAL
REM KrakenScout directory inside LOCALAPPDATA is removed.
if /i not "%TARGET%"=="%LOCALAPPDATA%\KrakenScout" goto :skip_kill
if exist "%TARGET%" rmdir /s /q "%TARGET%"
:skip_kill

echo Removing this bundle folder...
cd /d "%TEMP%"
start "" /b cmd /c "ping -n 3 127.0.0.1 >nul & rmdir /s /q "%~dp0""
exit
