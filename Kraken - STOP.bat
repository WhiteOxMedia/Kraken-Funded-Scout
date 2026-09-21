@echo off
title Kraken - STOP (full stack shutdown)
cd /d "%~dp0"

REM Kraken - STOP - the real OFF switch (rebuilt Sep 21; the original Sep 15
REM script was lost in the Sep 20 workspace deletion event, reconstructed
REM from the contracts launch_stack.ps1, start_detached.ps1 and
REM reminders/console_watchdog.ps1 pin on it).
REM
REM WHAT IT DOES, in order:
REM   1. Removes the console-watchdog marker (.console_watchdog.on) FIRST,
REM      so no heal tick can resurrect anything mid-shutdown ("a stop stays
REM      a stop"; the launcher's re-arm block restores it on next start);
REM   2. Drops stop_stack.lock (the launcher clears it on the next start);
REM   3. Kills BOTH halves of every component pair - the .venv shim parent
REM      AND its interpreter child, plus the visible tee wrappers and any
REM      hidden helper parents - by command-line shape, not window handle
REM      (closing a window kills only that one component);
REM   4. Verifies nothing of the shape survives and reports.
REM
REM NOT this script: the Kill Switch bats (FLATTEN/RESET) are the TRADING
REM halt - they touch positions, not processes. This one stops the fleet.

echo ============================================================
echo   KRAKEN - STOP: disarming watchdog, stopping all components
echo ============================================================

REM -- 1. watchdog first: a heal tick must not race the shutdown -------
if exist ".console_watchdog.on" (
    del /f /q ".console_watchdog.on" >nul 2>&1
    echo [OK] watchdog disarmed (.console_watchdog.on removed)
) else (
    echo [OK] watchdog already disarmed
)

REM -- 2. the stop lock: the launcher clears it on next start ----------
type nul > stop_stack.lock
echo [OK] stop_stack.lock armed

REM -- 3. sweep every component pair by command-line shape -------------
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
"$shapes = @('volatility_engine\.py','background_scanner\.py','bot_runner\.py','streamlit run app\.py');" ^
"$procs = Get-CimInstance Win32_Process;" ^
"$kill = @{};" ^
"foreach ($p in $procs) { if ($p.Name -match '^python' -and $p.CommandLine) { foreach ($s in $shapes) { if ($p.CommandLine -match $s) { $kill[[int]$p.ProcessId] = $true } } } };" ^
"foreach ($p in $procs) { if ($kill.ContainsKey([int]$p.ParentProcessId)) { $kill[[int]$p.ProcessId] = $true } };" ^
"$n = 0; foreach ($k in $kill.Keys) { if ($k -ne $PID) { Stop-Process -Id $k -Force -ErrorAction SilentlyContinue; $n++ } }; Write-Output (\"[OK] swept $n processes (both halves of every pair)\")"

REM -- 4. verify ---------------------------------------------------------
timeout /t 3 /nobreak >nul
set LEFT=0
for /f %%i in ('powershell -NoProfile -Command "(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python' -and $_.CommandLine -match 'volatility_engine\.py|background_scanner\.py|bot_runner\.py|streamlit run app\.py' } | Measure-Object).Count"') do set LEFT=%%i
if "%LEFT%"=="0" (
    echo [OK] fleet is down. Nothing will restart it - the watchdog is
    echo      disarmed and the Kraken desktop shortcut starts everything
    echo      again ^(and re-arms the watchdog^).
) else (
    echo [WARN] %LEFT% component process^(s^) still alive - re-run this bat,
    echo        or kill them by hand from Task Manager.
)
echo ============================================================
pause
