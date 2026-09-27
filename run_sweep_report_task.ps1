# run_sweep_report_task.ps1 - the CodebuffWeeklyOrphanReport runner.
#
# The task fires WEEKLY (Sunday 06:15, StartWhenAvailable so a missed
# Sunday runs when the machine next becomes available) AND on every
# boot/logon (the boot trigger is the catch-up safety net). This
# runner makes the boot firing safe: it keeps task_sweep_report_last.txt
# (epoch of the last ACTUAL run) and exits 0 without touching the DB
# when the weekly cadence already ran inside its window (7 days minus
# a 1h grace). A due run executes the read-only report (sweep_db.py
# --report - it can never write the DB), appends the receipt to
# logs_sweep_report.txt behind a UTC separator, stamps the marker, and
# EXITS WITH THE CLI's CODE (0 all-clear / 1 violations / 2 an edge is
# UNPLANNED) - that rc is Task Scheduler's Last Result, the alert
# surface. A crash before the stamp leaves the marker stale: the next
# firing retries, and the pool scheduler's dead-man leg alerts when
# the marker ages past 8 days. See "Scheduled alerting" in README.md.

$ErrorActionPreference = 'Stop'
$repo  = Split-Path -Parent $MyInvocation.MyCommand.Path
$stamp = Join-Path $repo 'task_sweep_report_last.txt'
$log   = Join-Path $repo 'logs_sweep_report.txt'

$now   = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$week  = 7 * 86400
$grace = 3600

$last = 0
if (Test-Path $stamp) {
    $raw = Get-Content $stamp -Raw -ErrorAction SilentlyContinue
    if ($raw) {
        [void][long]::TryParse($raw.Trim(),
            [System.Globalization.NumberStyles]::Integer,
            [System.Globalization.CultureInfo]::InvariantCulture, [ref]$last)
    }
}
# an unreadable marker parses as 0 -> DUE (fail toward running again)
if ($last -gt 0 -and ($now - $last) -lt ($week - $grace)) {
    exit 0
}

$py  = Join-Path $repo '.venv\Scripts\python.exe'
$cli = Join-Path $repo 'sweep_db.py'
Add-Content -Path $log -Value (
    "===== run " + (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss') +
    " UTC (the exit code of this run is the task's Last Result)") -Encoding UTF8
& $py $cli --report 2>&1 | Out-File -FilePath $log -Append -Encoding UTF8
$rc = $LASTEXITCODE
Set-Content -Path $stamp -Value ([string]$now) -Encoding Ascii
exit $rc
