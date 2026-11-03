# Kraken Scout - console watchdog (Sep 7, fleet-gated Sep 15)
#
# WHAT CHANGED (Sep 15 - the boot-opener fix):
# The old watchdog healed a dead console UNCONDITIONALLY, every minute,
# around the clock. Two symptoms fell out of that:
#   (a) at Windows unlock it could be the thing that STARTED a console
#       for the day - before the Kraken desktop shortcut ever ran (the
#       "it opens with startup, not the shortcut" report);
#   (b) it resurrected a console the user had just killed, so there was
#       no way to shut the stack down.
# The gate now: heal ONLY while the fleet is actually running - BOTH pid
# locks (scanner.lock in the scout repo, bot.lock in the bot repo) must
# name a live python. At boot, with no fleet, it starts NOTHING: the
# Kraken desktop shortcut is the only thing that starts the stack.
# (Kraken - STOP.bat removes the marker too, so a stop stays a stop.)
#
# Console detection is contract-shaped: the launcher's console is
#   python -m streamlit run app.py --server.port 8501 ...
# so "alive" means LISTENING on :8501, or a console process of that
# exact shape born <90s ago (boot race - a newborn has not bound yet).
# Anything of that shape alive but NOT serving and NOT a newborn is a
# WinError-64 zombie and gets swept - BOTH halves (the .venv shim parent
# AND its uv interpreter child; killing one half orphans the other).
#
# The spawn goes through start_detached.ps1 - the same hardened helper
# the launcher uses - instead of Start-Process -RedirectStandardOutput,
# which made the streamlit.exe trampoline's pid a lie and leaked the
# caller's inherited handles into the child.
#
# The console (Streamlit on Python 3.14/Windows) dies a silent death:
# the asyncio accept loop crashes with WinError 64 ("The specified
# network name is no longer available" - the IOCP accept_coro does not
# tolerate client-aborted connections) and the process LINGERS with no
# listening socket. Health checks that only look at the pid are fooled.
#
# Disable entirely: delete the marker file (or the scheduled task).

$marker = "C:\Users\birds\workspace\kraken_scout\.console_watchdog.on"
$scout  = "C:\Users\birds\workspace\kraken_scout"
$botDir = "C:\Users\birds\workspace\kraken_xrp_trading_bot"
$grace  = "$scout\.console_watchdog.grace"
$log    = "$scout\logs\console_watchdog.log"

if (-not (Test-Path $marker)) { exit 0 }

# ── THE GATE: no fleet, no heal ────────────────────────────────────────
# A watchdog that starts work nobody asked for is the boot-opener bug.
# Both components' pid locks must name a live python to justify healing.
function Test-LockAlive([string]$lockPath) {
    if (-not (Test-Path $lockPath)) { return $false }
    $lockPid = "$((Get-Content $lockPath -ErrorAction SilentlyContinue | Select-Object -First 1))".Trim()
    if ($lockPid -notmatch '^\d+$') { return $false }
    $row = tasklist /FI "PID eq $lockPid" /FO CSV /NH 2>$null | Select-Object -First 1
    # alive only if a live PYTHON holds the pid (recycled pids on other
    # exes are exactly the false positive this check exists to refuse)
    return [bool]($row -and "$row" -match 'python')
}
if (-not (Test-LockAlive "$scout\scanner.lock")) { exit 0 }
if (-not (Test-LockAlive "$botDir\bot.lock"))    { exit 0 }

# ── console alive? (contract-shaped) ───────────────────────────────────
function Test-ConsoleAlive {
    # 1) actually serving?
    if (netstat -ano | Select-String ":8501\s.*LISTENING") { return $true }
    # 2) our own recent spawn - a newborn console may not be listening yet
    if (Test-Path $grace) {
        $age = ((Get-Date) - (Get-Item $grace).LastWriteTime).TotalSeconds
        if ($age -lt 90) { return $true }
    }
    # 3) a contract-shaped console born moments ago (launcher race)
    $young = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match '^python' -and
        $_.CommandLine -match 'streamlit run app\.py' -and
        $_.CommandLine -match '8501' -and
        $_.CreationDate -and (((Get-Date) - $_.CreationDate).TotalSeconds -lt 90)
    }
    return [bool]$young
}
if (Test-ConsoleAlive) { exit 0 }

# ── zombie sweep: contract-shaped console alive but not serving ────────
# Kill BOTH halves - the shim parent (.venv python.exe) and its uv
# interpreter child - or the surviving half keeps the mystery doubles.
$targets = @{}
Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python' -and $_.CommandLine -match 'streamlit run app\.py'
} | ForEach-Object { $targets[[int]$_.ProcessId] = $true }
$all = Get-CimInstance Win32_Process
foreach ($p in $all) {
    if (-not $targets.ContainsKey([int]$p.ProcessId) -and
        $p.Name -match '^python' -and
        $targets.ContainsKey([int]$p.ParentProcessId)) {
        $targets[[int]$p.ProcessId] = $true
    }
}
foreach ($k in $targets.Keys) {
    Stop-Process -Id $k -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2

Add-Content -Path $log -Value "$(Get-Date -Format s) WATCHDOG: console listener dead (WinError 64 accept crash) - restarting (fleet gate passed, mode $(if (Test-Path (Join-Path $scout 'visible_stack.off')) { 'hidden' } else { 'visible' }))"

# ── respawn through the hardened helper (honest pid, rotated logs) ─────
# In the stack's own mode: visible when the fleet runs visible (a
# healthy fleet heals visibly - a hidden respawn would look like a
# component vanished), hidden when visible_stack.off is present.
$hargs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',"$scout\start_detached.ps1",`
           '-Component','streamlit','-LogStem','console','-Base',$scout)
if (-not (Test-Path (Join-Path $scout 'visible_stack.off'))) { $hargs += '-ShowWin' }
Start-Process -FilePath "powershell.exe" -ArgumentList $hargs `
    -WindowStyle Hidden | Out-Null

Set-Content -Path $grace -Value (Get-Date -Format s)
