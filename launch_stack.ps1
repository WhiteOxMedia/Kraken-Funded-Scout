# Launch stack check - hidden-window detached launch with REAL verification.
# Never kills anything; starts ONLY missing pieces.
#
# WHY THIS EXISTS (Sep 11): the old spawn was
#     Start-Process cmd.exe '/c', "python ... 2>&1 & pause"
# which parked a keyboard-less cmd window on `pause` forever, ran python
# through cmd's own working directory (a stranded CWD could silently
# no-start), never verified the child actually came up, and reported
# success at a black screen. A stale pid file then made the scanner's own
# single-instance guard refuse -- and the launcher never noticed.
#
# This launcher:
#   1. spawns each piece through start_detached.ps1 (hidden window, UTF-8,
#      explicit -WorkingDirectory, no pause trap, exit code + logs kept);
#   2. WAITS and verifies each piece is actually alive (its pid lock
#      names a live process) before moving on;
#   3. on a failed verify (e.g. the guard refused on a RECYCLED pid --
#      tasklist/OpenProcess see any process on that pid, not a scanner),
#      lets the helper take the stale lock back ONCE, then re-verifies;
#   4. exits 0 only when everything it started is verifiably up.
# Exit codes: 0 = all four components running when we finished; 1 = something
# failed to come up (check logs\scanner_stderr.log etc.).
#
# Usage:  powershell -NoProfile -ExecutionPolicy Bypass -File launch_stack.ps1

$ErrorActionPreference = "SilentlyContinue"

# ── LAUNCHER MUTEX ─────────────────────────────────────────────────────
# Only ONE launch_stack.ps1 may run at a time. If the shortcut is fired
# twice (double-click, or re-run while the first is still starting), the
# second instance would race the first: both see "nothing running" and
# both start everything = 2 of everything. The mutex makes the second
# instance exit immediately instead.
$launcherMutex = New-Object System.Threading.Mutex($false, "Global\KrakenLaunchStack")
if (-not $launcherMutex.WaitOne(0)) {
    Write-Host "============================================================"
    Write-Host "  [SKIP] A Kraken launcher is ALREADY RUNNING."
    Write-Host "  If nothing seems to be starting, give it a minute -"
    Write-Host "  it checks each component before starting anything."
    Write-Host "============================================================"
    Start-Sleep -Seconds 5
    exit 0
}

$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$botDir = "C:\Users\birds\Workspace\kraken_xrp_trading_bot"
Set-Location $base
$logsDir = Join-Path $base "logs"
if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir | Out-Null }
$helper = Join-Path $base "start_detached.ps1"

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

function Start-Verified([string]$component, [string]$logStem,
                        [string]$botDirForHelper) {
    # Spawn detached, then WAIT until the piece's pid lock names a live
    # process. Returns $true only when verifiably up.
    & $helper -Component $component -LogStem $logStem -Base $base `
              -BotDir $botDirForHelper
    Start-Sleep -Seconds 2
    $deadline = (Get-Date).AddSeconds(45)
    $retaken = $false
    while ((Get-Date) -lt $deadline) {
        if (Test-Component $component) { return $true }
        # the single-instance guard may have refused on a stale/recycled
        # pid file: one helper-run retake, then keep verifying
        if (-not $retaken) {
            $retaken = $true
            & $helper -Component $component -LogStem $logStem -Base $base `
                      -BotDir $botDirForHelper -RetakeStalePid
        }
        Start-Sleep -Seconds 3
    }
    return $false
}

Write-Host "============================================================"
Write-Host "  KRAKEN stack check - starts only what is missing"
Write-Host "============================================================"

$okAll = $true

# -- 1. Volatility Engine ------------------------------------------
if (Test-Component "volatility_engine") {
    Write-Host "[OK]  Volatility Engine already running - skipping"
} else {
    Write-Host "[NEW] Starting Volatility Engine (hidden, verified)..."
    if (Start-Verified "volatility_engine" "volatility_engine" "") { 
        Write-Host "[UP]  Volatility Engine is alive."
    } else {
        Write-Host "[FAIL] Volatility Engine did not come up - see logs\volatility_engine_stderr.log"
        $okAll = $false
    }
}

# -- 2. Scout Scanner ----------------------------------------------
if (Test-Component "background_scanner") {
    Write-Host "[OK]  Scout Scanner already running - skipping"
} else {
    Write-Host "[NEW] Starting Scout Scanner (hidden, verified)..."
    if (Start-Verified "background_scanner" "scanner" "") {
        Write-Host "[UP]  Scout Scanner is alive."
    } else {
        Write-Host "[FAIL] Scout Scanner did not come up - see logs\scanner_stderr.log"
        $okAll = $false
    }
}

# -- 3. Trader Bot --------------------------------------------------
if (Test-Component "bot_runner") {
    Write-Host "[OK]  Trader Bot already running - skipping"
} else {
    Write-Host "[NEW] Starting Trader Bot (hidden, verified)..."
    if (Start-Verified "bot_runner" "bot" $botDir) {
        Write-Host "[UP]  Trader Bot is alive."
    } else {
        Write-Host "[FAIL] Trader Bot did not come up - see logs\bot_stderr.log"
        $okAll = $false
    }
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
    Write-Host "Dashboard stopped. Engine / Scanner / Bot run detached (logs in logs\)."
    Read-Host "Press Enter to close"
    exit 0
}

Write-Host "============================================================"
if ($okAll) {
    Write-Host "  Stack is up. Voice = scanner (speaks detached). Logs in logs\."
} else {
    Write-Host "  STACK INCOMPLETE -- a piece failed its liveness check."
    Write-Host "  Check the logs\ folder before re-running."
}
Write-Host "============================================================"
Start-Sleep -Seconds 3
if ($okAll) { exit 0 } else { exit 1 }
