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
if exist "%TARGET%" rmdir /s /q "%TARGET%"

echo Removing this bundle folder...
cd /d "%TEMP%"
start "" /b cmd /c "ping -n 3 127.0.0.1 >nul & rmdir /s /q "%~dp0""
exit
