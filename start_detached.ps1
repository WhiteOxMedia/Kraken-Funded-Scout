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
# Sep 14: the CONSOLE (streamlit) is spawned through here too (-Component
# streamlit). It used to run in the launcher's foreground, which held the
# launcher alive for as long as the dashboard ran -- a sync caller's pipe
# hung until timeout on a SUCCESSFUL launch. Detached with its own logs,
# same as every other piece.
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
    [switch]$RetakeStalePid,
    [switch]$ShowWin
)

# Sep 15 - THE VISIBLE STACK (user request: "the python window(s) need to
# be visible to me, not running in the background - I need to see it
# working"). With -ShowWin the component spawns with a NORMAL console
# window titled after the component, its output TEE'D: live on the
# window AND transcribed to logs\<stem>_console.log (PowerShell 5.1 has
# no ForEach-Object -Parallel, and native exe output in the same pipeline
# as Tee-Object arrives line-buffered anyway, which is exactly what a
# watch-window wants). The file logs keep every other guarantee - the
# early-death capture appends to _stderr.log in both modes.
#
# WHY TEE AND NOT PURE REDIRECTION: a purely redirected visible window
# would show nothing. WHY TEE AND NOT PURE CONSOLE: the rotation glyph
# mojibake that motivated PYTHONIOENCODING=utf-8 applies to the console
# too, so the tee runs through utf8 as well.
#
# One consequence the user asked for and gets: with visible windows,
# CLOSING a component's window is a natural per-component shutdown -
# the shim pair dies with its console. Kraken - STOP remains the
# whole-stack off switch (it kills by command line either way), and the
# fleet-gated watchdog RESPAWNS a closed window while the fleet runs,
# visibly - a stop must go through Kraken - STOP.

$title = "Kraken - $Component"
# the command-line shape that proves this component is alive (the same
# shapes the launcher verifies with; 'streamlit' as a bare substring
# would false-match the tee's own log filename below)
$shape = switch ($Component) {
    "streamlit" { "streamlit run app.py" }
    default     { $Component }
}

$ErrorActionPreference = "SilentlyContinue"
Set-Location $Base

$logsDir = Join-Path $Base "logs"
if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir | Out-Null }

# Sep 14: the helper runs as its OWN hidden process (the launcher's
# handle-severing spawn), so Write-Host goes nowhere. Every piece of
# evidence also lands in logs\spawn_<stem>.log.
$spawnLog = Join-Path $logsDir "spawn_$LogStem.log"
function Say([string]$m) {
    Write-Host $m
    Add-Content -Path $spawnLog -Value "$(Get-Date -Format s) $m" `
        -ErrorAction SilentlyContinue
}

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
    "streamlit" {
        # the console: run via `python -m streamlit`, NOT streamlit.exe —
        # the .exe is a pip trampoline that spawns python as a child and
        # exits, which would trip the 5s early-death check below and
        # orphan the real process. -m keeps the pid handle honest.
        # Verified by the launcher via the :8501 listener / cmdline
        # pattern; no pid lock of its own.
        $script = "-m"; $py = "$Base\.venv\Scripts\python.exe"
        # --server.headless true: streamlit must NEVER open its own
        # browser tab (the Sep 15 double-tab bug: streamlit auto-opened
        # one AND the launcher opened one - two tabs per start, plus a
        # surprise tab from every watchdog heal). The LAUNCHER is the
        # single browser-opener, on both the fresh and already-running
        # paths.
        $extra = "streamlit run app.py --server.port 8501 --server.runOnSave false --server.headless true"
        $lockFile = ""
    }
    default { Say "[helper] unknown component '$Component'"; exit 2 }
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
        Say "[helper] cleared stale pid lock $lockFile (was '$old')"
    } else {
        Say "[helper] pid $old is a live python - lock left in place"
    }
}

# ── rotate last boot's logs (one generation kept, never overwritten) ───
$outLog = Join-Path $logsDir "${LogStem}_stdout.log"
$errLog = Join-Path $logsDir "${LogStem}_stderr.log"
if ($ShowWin) {
    # visible mode: the live stream IS the console, tee'd into a
    # transcript; rotate THAT one generation instead of the (unused)
    # stdout redirect
    $conLog = Join-Path $logsDir "${LogStem}_console.log"
    if (Test-Path $conLog) { Move-Item -Force $conLog "$conLog.prev" }
} else {
    if (Test-Path $outLog) { Move-Item -Force $outLog "$outLog.prev" }
    if (Test-Path $errLog) { Move-Item -Force $errLog "$errLog.prev" }
}

# ── spawn: no cmd, no pause, UTF-8 stdio, explicit cwd ───────────────
# Hidden mode (default):   python -X utf8 <script> with separate
#   stdout/stderr redirect handles.
# Visible mode (-ShowWin): the SAME argv in a NORMAL console window
#   titled 'Kraken - <component>', its live output tee'd — on the
#   window AND transcribed line-by-line (Add-Content, so the transcript
#   is live too) to logs\<stem>_console.log. The tee wrapper is its own
#   parameterized script (no interpolation traps), dies with its child,
#   and keeps the window's title.
$oldPyEnc = $env:PYTHONIOENCODING
$env:PYTHONIOENCODING = "utf-8"
$argList = @("-X", "utf8", "`"$script`"")
if ($extra) { $argList += $extra.Split(" ") }
if ($ShowWin) {
    $conLog = Join-Path $logsDir "${LogStem}_console.log"
    $teeScript = Join-Path $logsDir "_tee_$LogStem.ps1"
    @'
param([string]$Py, [string]$ChildArgs, [string]$Title, [string]$Cwd, [string]$ConLog)
$host.UI.RawUI.WindowTitle = $Title
# CLAIM VISIBILITY: a window spawned deep in a hidden chain inherits the
# parent's show-state no matter what -WindowStyle promised, so the
# wrapper shows its own console explicitly (SW_SHOW). Self-service
# beats inheritance.
Add-Type -Namespace Native -Name Win -MemberDefinition '
[DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow();
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);'
$hwnd = [Native.Win]::GetConsoleWindow()
if ($hwnd -ne [IntPtr]::Zero) { [void][Native.Win]::ShowWindow($hwnd, 5) }
$env:PYTHONIOENCODING = "utf-8"
Set-Location $Cwd
# ChildArgs arrives space-joined (an array cannot survive a -File
# spawn: tokens like --server.port would be misread as parameters).
# Splitting on whitespace is safe: no stack argument contains a space.
& $Py ($ChildArgs -split '\s+') 2>&1 | ForEach-Object {
    $line = ($_ | Out-String).TrimEnd()
    Write-Host $line
    Add-Content -Path $ConLog -Value $line -Encoding UTF8
}
# the child's exit code becomes the wrapper's (the early-death capture
# below reads it to name a cause)
exit $LASTEXITCODE
'@ | Set-Content -Path $teeScript -Encoding UTF8
    $childArgs = @("-X", "utf8")
    if ($script -eq "-m") {
        $childArgs += @("-m") + ($extra.Split(" "))
    } else {
        $childArgs += @($script)
        if ($extra) { $childArgs += ($extra.Split(" ")) }
    }
    $proc = Start-Process -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", `
                        "-File", "`"$teeScript`"", `
                        "-Py", "`"$py`"", `
                        "-ChildArgs", "`"$($childArgs -join ' ')`"", `
                        "-Title", "`"$title`"", `
                        "-Cwd", "`"$cwd`"", `
                        "-ConLog", "`"$conLog`"") `
        -WorkingDirectory $cwd -WindowStyle Normal -PassThru
    if ($proc) {
        Say "[helper] spawned $Component pid $($proc.Id) (VISIBLE, title '$title') -> window + logs\$LogStem _console.log"
    }
} else {
    $proc = Start-Process -FilePath $py -ArgumentList $argList `
            -WorkingDirectory $cwd -WindowStyle $(if ($ShowWin) { 'Normal' } else { 'Hidden' }) `
            -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
    if ($proc) {
        Say "[helper] spawned $Component pid $($proc.Id) ($(if ($ShowWin) { 'VISIBLE' } else { 'hidden' })) -> logs\$LogStem `_*.log"
    }
}
if ($oldPyEnc -ne $null) { $env:PYTHONIOENCODING = $oldPyEnc } else { Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue }

if (-not $proc) {
    Say "[helper] SPAWN FAILED for $Component (Start-Process returned nothing)"
    exit 3
}

# ── capture an EARLY death instead of reporting phantom success ────────
# Visible mode: the tee wrapper BLOCKS on the python child, so "wrapper
# alive after 5s" IS "component alive"; an instantly-dying child closes
# the pipeline, the wrapper exits with the child's code, and the death
# is captured — into the transcript the user actually reads.
Start-Sleep -Seconds 5
if ($ShowWin) {
    $evidenceLog = Join-Path $logsDir "${LogStem}_console.log"
    $evidenceName = "${LogStem}_console.log"
} else {
    $evidenceLog = $errLog
    $evidenceName = "${LogStem}_stderr.log"
}
if ($proc.HasExited) {
    $code = $proc.ExitCode
    if ($null -eq $code -or "$code" -eq "") { $code = "unknown (exit handle lost)" }
    Add-Content -Path $evidenceLog -Value "[start_detached] $Component exited immediately, exit code $code ($(Get-Date -Format s))"
    Say "[helper] $Component DIED at boot (exit $code) - see logs\$evidenceName"
    exit 4
}
Add-Content -Path $evidenceLog -Value "[start_detached] $Component booted $(if ($ShowWin) { 'VISIBLE' } else { 'detached' }), pid $($proc.Id), $(Get-Date -Format s)"
Say "[helper] $Component still running after 5s (pid $($proc.Id))"
exit 0
