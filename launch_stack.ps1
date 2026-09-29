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
#   4. exits 0 only when everything it started is verifiably up; the
#      console is spawned DETACHED too (never in this window's
#      foreground), so sync callers are never held by a running child.
# Exit codes: 0 = all four components running when we finished; 1 = something
# failed to come up (check logs\scanner_stderr.log etc.).
#
# SYNC-SAFE (Sep 14): every component -- the console included -- is
# spawned detached and verified by liveness probes; this script NEVER
# runs a component in its own foreground and NEVER waits on a child for
# the life of that child. A synchronous invocation (cmd /c wait, a
# script harness, an agent's terminal tool) therefore returns as soon
# as verification completes, never at the child's exit.
#
# Usage:  powershell -NoProfile -ExecutionPolicy Bypass -File launch_stack.ps1

param(
    [switch]$Visible,
    [switch]$Hidden
)

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
    Start-Sleep -Seconds 2
    exit 0
}

$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$botDir = "C:\Users\birds\Workspace\kraken_xrp_trading_bot"
Set-Location $base
$logsDir = Join-Path $base "logs"
if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir | Out-Null }
$helper = Join-Path $base "start_detached.ps1"

# ── VISIBILITY MODE (Sep 15: "the python window(s) need to be visible
# to me, not running in the background - I need to see it working") ────
# Default = VISIBLE: one titled console per component ('Kraken -
# <component>'), live output tee'd into logs\<stem>_console.log. Drop a
# file named visible_stack.off in the repo root to return to the old
# all-hidden fleet. -Visible / -Hidden override the marker for one run
# (and -Hidden never flips her saved preference).
$showWin = -not (Test-Path (Join-Path $base "visible_stack.off"))
if ($Hidden)  { $showWin = $false }
if ($Visible) { $showWin = $true }

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
                        [string]$botDirForHelper, [switch]$ShowWin) {
    # visible mode verifies the CONSOLE shape ('streamlit run app.py'),
    # not the bare component name (which would false-match the tee's
    # own wrapper/log names)
    $probe = if ($ShowWin -and $component -eq "streamlit") {
        "streamlit run app.py"
    } else { $component }
    $hargs = @("-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", "`"$helper`"",
               "-Component", $component, "-LogStem", $logStem,
               "-Base", "`"$base`"",
               "-BotDir", "`"$botDirForHelper`"")
    if ($ShowWin) { $hargs += "-ShowWin" }
    # Spawn detached, then WAIT until the piece's pid lock names a live
    # process. Returns $true only when verifiably up.
    # Sep 14 HANDLE SEVERING: the helper runs as its OWN hidden process
    # (a UseShellExecute spawn inherits NO handles), never as a script
    # child. A script child carries this launcher's std handles into the
    # spawned component, and Windows PowerShell 5.1's Start-Process
    # (bInheritHandles=TRUE) lets a redirected child inherit EVERY
    # inheritable handle of its parent -- so a sync caller's stdout pipe
    # ended up held by the component for its whole life even after this
    # launcher exited (the launcher-returned-but-pipe-held bug). With the
    # helper's own handle set clean, the component inherits only the
    # helper's, and the chain to the caller's pipe is broken. The
    # helper's evidence lands in logs\spawn_<stem>.log instead of a
    # (now invisible) hidden console.
    Start-Process -FilePath "powershell.exe" -WindowStyle Hidden `
        -ArgumentList $hargs
    Start-Sleep -Seconds 2
    $deadline = (Get-Date).AddSeconds(45)
    $retaken = $false
    while ((Get-Date) -lt $deadline) {
        if (Test-Component $probe) { return $true }
        # the single-instance guard may have refused on a stale/recycled
        # pid file: one helper-run retake, then keep verifying
        if (-not $retaken) {
            $retaken = $true
            Start-Process -FilePath "powershell.exe" -WindowStyle Hidden `
                -ArgumentList ($hargs + "-RetakeStalePid")
        }
        Start-Sleep -Seconds 3
    }
    return $false
}

Write-Host "============================================================"
Write-Host "  KRAKEN stack check - starts only what is missing"
if ($showWin) {
    Write-Host "  MODE: VISIBLE - one window per component (closing a"
    Write-Host "  window stops that component; STOP bat stops everything)"
} else {
    Write-Host "  MODE: hidden - components run detached, logs in logs\"
}
Write-Host "============================================================"

$okAll = $true

# -- 1. Volatility Engine ------------------------------------------
if (Test-Component "volatility_engine") {
    Write-Host "[OK]  Volatility Engine already running - skipping"
} else {
    Write-Host "[NEW] Starting Volatility Engine (hidden, verified)..."
    if (Start-Verified "volatility_engine" "volatility_engine" "" -ShowWin:$showWin) { 
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
    if (Start-Verified "background_scanner" "scanner" "" -ShowWin:$showWin) {
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
    if (Start-Verified "bot_runner" "bot" $botDir -ShowWin:$showWin) {
        Write-Host "[UP]  Trader Bot is alive."
    } else {
        Write-Host "[FAIL] Trader Bot did not come up - see logs\bot_stderr.log"
        $okAll = $false
    }
}

# -- 4. Console Dashboard -------------------------------------------
# Sep 14: the console is spawned DETACHED through start_detached.ps1 like
# every other piece. It used to run in this launcher's foreground ("this
# window becomes the dashboard") + a Read-Host trap, which held the
# launcher alive for as long as the dashboard ran: a sync caller's pipe
# hung until timeout on a SUCCESSFUL launch, and the launcher mutex was
# held the whole time (later launches saw "[SKIP] already running").
# Now a sync invocation returns as soon as the console verifiably listens.
Start-Sleep -Seconds 2
$consoleUp = $false
foreach ($line in (netstat -an)) {
    if ($line -match ":8501\s.*LISTENING") { $consoleUp = $true; break }
}if ($consoleUp) {
    Write-Host "[OK]  Console already running - skipping"
    # every click lands on the dashboard, even when nothing needed
    # starting - and the LAUNCHER is the single browser-opener    # (streamlit runs headless, so it never opens its own tab)
    Start-Process "http://localhost:8501"
} else {
    Write-Host "[NEW] Starting Console (detached, hidden, verified)..."
    if (Start-Verified "streamlit" "console" "" -ShowWin:$showWin) {
        Write-Host "[UP]  Console is alive on :8501."
        Start-Process "http://localhost:8501"   # double-click UX parity:
        # the dashboard is detached now, so the browser IS the window
    } else {
        Write-Host "[FAIL] Console did not come up - see logs\console_stderr.log"
        $okAll = $false
    }
}

# -- 4b. Hindsight API (the console panel's live feed) ---------------
# Sep 29: the panel reads 127.0.0.1:8785, so the service joins the
# stack: verified by the PORT (netstat LISTENING, the launcher's own
# console pattern), spawned through the hardened helper. Its pid file
# and single-instance contract live inside hindsight_service itself.
$hindsightUp = $false
foreach ($line in (netstat -an)) {
    if ($line -match ":8785\s.*LISTENING") { $hindsightUp = $true; break }
}
if ($hindsightUp) {
    Write-Host "[OK]  Hindsight API already running - skipping"
} else {
    Write-Host "[NEW] Starting Hindsight API (detached, hidden, verified)..."
    if ((Start-Verified "hindsight_service" "hindsight" "" -ShowWin:$showWin) -and
        # the cmdline probe proves a live python; the PORT proves a
        # serving API (a cmdline match alone could be a dying bind)
        -not $false) {
        $bound = $false
        $deadline = (Get-Date).AddSeconds(15)
        while ((Get-Date) -lt $deadline -and -not $bound) {
            foreach ($line in (netstat -an)) {
                if ($line -match ":8785\s.*LISTENING") { $bound = $true; break }
            }
            if (-not $bound) { Start-Sleep -Seconds 1 }
        }
        if ($bound) {
            Write-Host "[UP]  Hindsight API is alive on :8785."
        } else {
            Write-Host "[FAIL] Hindsight API process ran but never bound :8785 - see logs\hindsight_stderr.log"
            $okAll = $false
        }
    } else {
        Write-Host "[FAIL] Hindsight API did not come up - see logs\hindsight_stderr.log"
        $okAll = $false
    }
}

# -- 5. Watchdog re-arm -------------------------------------------------
# A stack this launcher confirmed UP is a stack the console watchdog may
# guard again: restore the marker (Kraken - STOP removed it) and clear
# the stop lock. Starting the stack by hand is the only thing that
# re-arms the watchdog -- boot never does (the gated watchdog heals
# nothing while the fleet is down, so unlock starts nothing).
if ($okAll) {
    Set-Content -Path (Join-Path $base ".console_watchdog.on") -Value (Get-Date -Format s)
    Remove-Item (Join-Path $base "stop_stack.lock") -Force -ErrorAction SilentlyContinue
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
