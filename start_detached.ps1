# start_detached.ps1 - spawn one stack component DETACHED and TRUSTWORTHILY.
#
# Used by launch_stack.ps1. One component per call:
#     powershell -NoProfile -ExecutionPolicy Bypass -File start_detached.ps1 `
#         -Component background_scanner -LogStem scanner -Base <scout dir>
# Add -RetakeStalePid to first clear a component's pid lock WHEN IT NAMES A
# DEAD OR NON-PYTHON PROCESS (the recycled-pid trap: tasklist/OpenProcess
# see ANY process on that pid, so the scanner's own guard can refuse a
# launch forever after a crash -- the launcher only calls this after it has
# confirmed no live component process exists, so clearing is safe).
#
# What "trustworthy" means here:
#   - hidden window (no black box to misread), NO cmd, NO pause trap;
#   - explicit -WorkingDirectory (the child never inherits a stranded CWD);
#   - PYTHONIOENCODING=utf-8 for the child (redirected stdio no longer
#     falls back to cp1252 mojibake; stdio-only, file reads unaffected);
#   - stdout/stderr redirected to logs\<stem>_stdout.log / _stderr.log
#     (rotated one generation to .prev so a spawn never silently overwrites
#     the evidence of the previous boot);
#   - the child's early exit is CAPTURED: if it dies within 5s, the exit
#     code is appended to the stderr log so the launcher's failure report
#     names a cause instead of a black screen.

param(
    [Parameter(Mandatory = $true)][string]$Component,
    [Parameter(Mandatory = $true)][string]$LogStem,
    [Parameter(Mandatory = $true)][string]$Base,
    [string]$BotDir = "",
    [switch]$RetakeStalePid
)

$ErrorActionPreference = "SilentlyContinue"
Set-Location $Base

$logsDir = Join-Path $Base "logs"
if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir | Out-Null }

# ── component table ────────────────────────────────────────────────────
$script = ""; $cwd = $Base; $py = ""; $extra = ""; $lockFile = ""
switch ($Component) {
    "background_scanner" {
        $script = "background_scanner.py"; $py = "$Base\.venv\Scripts\python.exe"
        $lockFile = "$Base\.scanner.pid"
    }
    "volatility_engine" {
        $script = "volatility_engine.py"; $py = "$Base\.venv\Scripts\python.exe"
        $lockFile = ""   # no own guard; the launcher verifies by command line
    }
    "bot_runner" {
        $script = "bot_runner.py"; $cwd = $BotDir
        $py = "$BotDir\.venv\Scripts\python.exe"
        $extra = "--interval 15"; $lockFile = "$BotDir\bot.lock"
    }
    default { Write-Host "[helper] unknown component '$Component'"; exit 2 }
}

# ── optional stale-pid retake (dead or NON-python pid on the lock) ─────
if ($RetakeStalePid -and $lockFile -and (Test-Path $lockFile)) {
    $old = (Get-Content $lockFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    $old = "$old".Trim()
    $alive = $false
    if ($old -match '^\d+$') {
        $row = tasklist /FI "PID eq $old" /FO CSV /NH 2>$null | Select-Object -First 1
        # alive only if a live PYTHON holds the pid (recycled pids on other
        # exes are exactly the false-refusal this retake exists to clear)
        if ($row -and "$row" -match 'python') { $alive = $true }
    }
    if (-not $alive) {
        Remove-Item $lockFile -Force
        Write-Host "[helper] cleared stale pid lock $lockFile (was '$old')"
    } else {
        Write-Host "[helper] pid $old is a live python - lock left in place"
    }
}

# ── rotate last boot's logs (one generation kept, never overwritten) ───
$outLog = Join-Path $logsDir "${LogStem}_stdout.log"
$errLog = Join-Path $logsDir "${LogStem}_stderr.log"
if (Test-Path $outLog) { Move-Item -Force $outLog "$outLog.prev" }
if (Test-Path $errLog) { Move-Item -Force $errLog "$errLog.prev" }

# ── spawn: hidden window, no cmd, no pause, UTF-8 stdio, explicit cwd ──
$oldPyEnc = $env:PYTHONIOENCODING
$env:PYTHONIOENCODING = "utf-8"
$argList = @("-X", "utf8", "`"$script`"")
if ($extra) { $argList += $extra.Split(" ") }
$proc = Start-Process -FilePath $py -ArgumentList $argList `
        -WorkingDirectory $cwd -WindowStyle Hidden `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
if ($oldPyEnc -ne $null) { $env:PYTHONIOENCODING = $oldPyEnc } else { Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue }

if (-not $proc) {
    Write-Host "[helper] SPAWN FAILED for $Component (Start-Process returned nothing)"
    exit 3
}
Write-Host "[helper] spawned $Component pid $($proc.Id) (hidden) -> logs\$LogStem `_*.log"

# ── capture an EARLY death instead of reporting phantom success ────────
Start-Sleep -Seconds 5
if ($proc.HasExited) {
    $code = $proc.ExitCode
    if ($null -eq $code -or "$code" -eq "") { $code = "unknown (exit handle lost)" }
    Add-Content -Path $errLog -Value "[start_detached] $Component exited immediately, exit code $code ($(Get-Date -Format s))"
    Write-Host "[helper] $Component DIED at boot (exit $code) - see logs\${LogStem}_stderr.log"
    exit 4
}
Add-Content -Path $errLog -Value "[start_detached] $Component booted detached, pid $($proc.Id), $(Get-Date -Format s)"
Write-Host "[helper] $Component still running after 5s (pid $($proc.Id))"
exit 0
