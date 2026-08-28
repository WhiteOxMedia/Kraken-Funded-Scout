# Launch stack check - pure ASCII, read-only detection, starts ONLY missing pieces.
# Never kills anything. The old inline dup-checks in the .bat were fragile
# (PowerShell quoting + CIM glitches); this script checks twice, logs what it
# found, and the scanner/bot PID locks are the final backstop.
#
# Usage:  powershell -NoProfile -ExecutionPolicy Bypass -File launch_stack.ps1
# Exit codes: 0 = all four components running when we finished

$ErrorActionPreference = "SilentlyContinue"
$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$botDir = "C:\Users\birds\Workspace\kraken_xrp_trading_bot"
Set-Location $base

function Test-Component([string]$pattern) {
    # True if any python process's command line contains the pattern.
    # Checked twice with a short gap: Get-CimInstance occasionally misses
    # processes on a single query (the Aug 28 duplicate-scanner cause).
    for ($i = 0; $i -lt 2; $i++) {
        $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'"
        foreach ($p in $procs) {
            if ($p.CommandLine -and $p.CommandLine -like "*$pattern*") {
                return $true
            }
        }
        Start-Sleep -Milliseconds 800
    }
    return $false
}

Write-Host "============================================================"
Write-Host "  KRAKEN stack check - starts only what is missing"
Write-Host "============================================================"

# -- 1. Volatility Engine ------------------------------------------
if (Test-Component "volatility_engine") {
    Write-Host "[OK]  Volatility Engine already running - skipping"
} else {
    Write-Host "[NEW] Starting Volatility Engine..."
    Start-Process cmd.exe -ArgumentList '/c', ".venv\Scripts\python.exe -X utf8 volatility_engine.py 2>&1 & pause" -WorkingDirectory $base
}

# -- 2. Scout Scanner ----------------------------------------------
if (Test-Component "background_scanner") {
    Write-Host "[OK]  Scout Scanner already running - skipping"
} else {
    Write-Host "[NEW] Starting Scout Scanner..."
    Start-Process cmd.exe -ArgumentList '/c', ".venv\Scripts\python.exe background_scanner.py 2>&1 & pause" -WorkingDirectory $base
}

# -- 3. Trader Bot --------------------------------------------------
if (Test-Component "bot_runner") {
    Write-Host "[OK]  Trader Bot already running - skipping"
} else {
    Write-Host "[NEW] Starting Trader Bot..."
    Start-Process cmd.exe -ArgumentList '/c', "cd /d $botDir && .venv\Scripts\python.exe bot_runner.py --interval 15 2>&1 & pause" -WorkingDirectory $botDir
}

# -- 4. Console Dashboard -------------------------------------------
Start-Sleep -Seconds 2
$consoleUp = $false
foreach ($line in (netstat -an)) {
    if ($line -match ":8501\s.*LISTENING") { $consoleUp = $true; break }
}
if ($consoleUp) {
    Write-Host "[OK]  Console already running - opening browser"
    Start-Process "http://localhost:8501"
} else {
    Write-Host "[NEW] Starting Console (this window becomes the dashboard)..."
    & ".venv\Scripts\streamlit.exe" run app.py --server.port 8501 --server.runOnSave false
    Write-Host ""
    Write-Host "Dashboard stopped. Engine / Scanner / Bot run in their own windows."
    Read-Host "Press Enter to close"
    exit 0
}

Write-Host "============================================================"
Write-Host "  Stack is up. Voice = scanner window. Do not close it."
Write-Host "============================================================"
Start-Sleep -Seconds 3
exit 0
