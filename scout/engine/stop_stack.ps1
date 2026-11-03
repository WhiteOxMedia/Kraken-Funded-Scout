# stop_stack.ps1 - THE REAL SHUTDOWN (Sep 15).
#
# WHY THIS EXISTS: the Sep 7 venv rebuild made every component run as a
# pair of processes - a 241KB .venv shim (python.exe) whose real
# interpreter runs as its identical child. Task Manager shows doubles,
# and killing the visible half orphans the other: the stack seemed
# unkillable. The old "close the window" shutdown also left the
# console-watchdog task alive, which resurrected a console within a
# minute. This script is the complete OFF:
#   1. takes the launcher mutex (no launcher races a shutdown);
#   2. kills EVERYTHING matching the stack's contract shapes - by
#      COMMAND LINE, not by window or pid file - including both halves
#      of every shim pair (a child whose parent dies is still matched
#      and killed; a two-pass sweep catches grandchildren);
#   3. removes the watchdog marker (disarm) and drops stop_stack.lock;
#   4. verifies silence, twice, and reports anything that survived.
#
# SAFE TO RUN ANYTIME: it touches only processes whose command lines
# match this project's own components, plus its own lock/marker files.
# Usage:  powershell -NoProfile -ExecutionPolicy Bypass -File stop_stack.ps1
#         (Kraken - STOP.bat wraps it from the desktop.)

$ErrorActionPreference = "SilentlyContinue"

$base   = Split-Path -Parent $MyInvocation.MyCommand.Path
$botDir = "C:\Users\birds\Workspace\kraken_xrp_trading_bot"
$marker = Join-Path $base ".console_watchdog.on"
$stopLock = Join-Path $base "stop_stack.lock"
$log    = Join-Path $base "logs\stop_stack.log"

# Only ONE stack-wide operation at a time (launch vs stop).
$mutex = New-Object System.Threading.Mutex($false, "Global\KrakenLaunchStack")
if (-not $mutex.WaitOne(0)) {
    Write-Host "[BUSY] A Kraken launcher is running - waiting for it to finish..."
    if (-not $mutex.WaitOne(45000)) {
        Write-Host "[FAIL] Launcher did not release within 45s - try again."
        exit 1
    }
}

function Test-StackProc {
    # A live python whose command line matches a stack component shape:
    # scanner, bot, volatility engines, console, hindsight API. Matches
    # BOTH halves of every shim pair (their command lines are identical
    # by design).
    $found = @{}
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ForEach-Object {
        $cl = "$($_.CommandLine)"
        if (-not $cl) { return }
        if ($cl -match "background_scanner" -or
            $cl -match "bot_runner" -or
            $cl -match "volatility_engine" -or
            $cl -match "streamlit run app\.py" -or
            $cl -match "hindsight_service\.py") {
            $found[[int]$_.ProcessId] = $cl
        }
    }
    return $found
}

function Get-Family {
    # The named pids PLUS any python whose parent is in the set
    # (the .venv shim parent <-> interpreter child pairs).
    param([hashtable]$seeds)
    $all  = Get-CimInstance Win32_Process -Filter "Name='python.exe'"
    $set  = @{}
    foreach ($k in $seeds.Keys) { $set[$k] = $true }
    foreach ($p in $all) {
        if ($set.ContainsKey([int]$p.ParentProcessId)) { $set[[int]$p.ProcessId] = $true }
    }
    return $set
}

# ---- 1. disarm the watchdog FIRST (no resurrection mid-shutdown) ----
Remove-Item $marker -Force -ErrorAction SilentlyContinue
Set-Content -Path $stopLock -Value ("stopped " + (Get-Date -Format s))

Write-Host "============================================================"
Write-Host "  KRAKEN - STOP: shutting the stack down"
Write-Host "============================================================"

# ---- 2. kill by contract shape, both halves, two passes ----
$pass = 0
while ($true) {
    $pass++
    $seeds = Test-StackProc
    if ($seeds.Count -eq 0) { break }
    if ($pass -gt 4) { break }
    $family = Get-Family $seeds
    Write-Host ("[KILL] pass {0}: {1} process(es)" -f $pass, $family.Count)
    foreach ($pid_ in $family.Keys) {
        Stop-Process -Id $pid_ -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
}

# ---- 3. verify silence ----
Start-Sleep -Seconds 2
$left1 = Test-StackProc
Start-Sleep -Seconds 3
$left2 = Test-StackProc

$lockNote = ""
if (Test-Path (Join-Path $base "scanner.lock")) {
    $lockNote = "  (scanner.lock left for the guard to re-take; pid is dead)"
}

Add-Content -Path $log -Value "$(Get-Date -Format s) STOP: shutdown requested; $(($left2.Count)) process(es) survived"
if ($left2.Count -eq 0) {
    Write-Host "[OK]  Stack is DOWN - every component, both halves of each pair."
    Write-Host "      Watchdog disarmed (marker removed) - nothing will restart."
    Write-Host "      Next start: the Kraken desktop shortcut (it re-arms the watchdog)."
    if ($lockNote) { Write-Host $lockNote }
    $mutex.ReleaseMutex()
    exit 0
} else {
    Write-Host "[WARN] $(($left2.Count)) process(es) survived two kill passes:"
    foreach ($kv in $left2.GetEnumerator()) {
        Write-Host ("   pid {0}: {1}" -f $kv.Key, ($kv.Value -replace '\s+', ' ').Substring(0, [Math]::Min(120, $kv.Value.Length)))
    }
    Write-Host "      They did not match the stack contract - inspect before killing."
    $mutex.ReleaseMutex()
    exit 1
}
