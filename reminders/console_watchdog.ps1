# Kraken Scout - console watchdog (Sep 7)
#
# The console (Streamlit on Python 3.14/Windows) dies a silent death:
# the asyncio accept loop crashes with WinError 64 ("The specified
# network name is no longer available" - the IOCP accept_coro does not
# tolerate client-aborted connections) and the process LINGERS with no
# listening socket. Health checks that only look at the pid are fooled.
#
# This watchdog: while the marker file exists and nothing is LISTENING
# on 8501, sweep zombie streamlit processes and start a fresh detached
# console. Delete the marker file (or the scheduled task) to disable.
#
# The real fix is a CPython patch upgrade of the venv's 3.14 base -
# left to the user (live system, mid-contest).

$marker = "C:\Users\birds\workspace\kraken_scout\.console_watchdog.on"
$log    = "C:\Users\birds\OneDrive\Documents\WindowsPowerShell\.freebuff\preview-e7fc8087-071d-44cc-8086-003a78246527.log"
$grace  = "C:\Users\birds\workspace\kraken_scout\.console_watchdog.grace"

if (-not (Test-Path $marker)) { exit 0 }

# grace window: a console started <90s ago may not be listening yet -
# never kill a newborn
if (Test-Path $grace) {
    $age = ((Get-Date) - (Get-Item $grace).LastWriteTime).TotalSeconds
    if ($age -lt 90) { exit 0 }
}

$listening = netstat -ano | Select-String ":8501\s.*LISTENING"
if ($listening) { exit 0 }

# zombie sweep: streamlit/app.py processes alive but not serving
Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match "python|streamlit" -and $_.CommandLine -match "app.py"
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Start-Sleep -Seconds 2

"$(Get-Date -Format s) WATCHDOG: console listener dead (WinError 64 accept crash) - restarting" |
    Out-File $log -Append -Encoding utf8

Start-Process -FilePath 'C:\Users\birds\workspace\kraken_scout\.venv\Scripts\streamlit.exe' `
    -ArgumentList 'run','app.py','--server.port','8501','--server.address','127.0.0.1','--server.headless','true','--server.runOnSave','false' `
    -WorkingDirectory 'C:\Users\birds\workspace\kraken_scout' `
    -RedirectStandardOutput $log `
    -RedirectStandardError "$log.err" `
    -WindowStyle Hidden | Out-Null

Set-Content -Path $grace -Value (Get-Date -Format s)
