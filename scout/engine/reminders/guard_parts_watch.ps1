# reminders/guard_parts_watch.ps1 - THE GUARD-PARTS NIGHTLY WATCH
# (user ask, Oct 8): every evening, watch the guard's own parts - the
# protection dial disc_mod, the learning channel mlt_corr, the burial
# share, the machine's factor - project the x0.9 recovery against the
# Oct 10 / Oct 11 tripwires, push the trajectory to the phone, and log
# the series on the record. The scheduled task fires this nightly at
# 20:30 (the evening boundary); the VBS launcher idiom keeps the
# desktop clean (the extension tripwire's pattern).
#
# THE RECOVERY MATH (from today's live read):
#   penalty_extra decays x0.9 per CLEAN SAL close (violations==0);
#   today 1.93 (the day's docks already cooled it from the Oct-7 2.43)
#   -> from tonight's snapshot, N days of clean closes put the extra at
#   extra * 0.9^N. Oct 10 = +2 evenings, Oct 11 = +3 evenings.
#   THE PROJECTION: the project's bands say the dock's own docstring
#   (meta_layers.dock_silent_rotation) prices the per-rotation cap at
#   2.0 - the Oct 10 tripwire is "the extra still above 2.0 on Oct 10
#   evening" (a lesson that has not decayed by its window) and the Oct
#   11 tripwire is "disc_mod still under 0.6 six evenings out" (the
#   band where the guard factor's discipline component stops limiting
#   the book). < 0.6 because the protection dial stops dominating once
#   it climbs back above the neutral 0.6 line (the CAREFUL band's low
#   edge in the plate's color logic: >= 60 uncapped is amber-to-green).
#
# CONTRACT (the extension tripwire's discipline, carried):
#   - Fail-soft: any read error is a SKIPPED leg, never a throw - the
#     watcher reports what it read and moves on.
#   - Windowless: registered through the VBS launcher (wscript, GUI
#     subsystem); the task itself is system-owned.
#   - ON THE RECORD: every evening appends one JSONL row to
#     scratch/_guard_parts_history.jsonl (the trajectory is computed
#     FROM the history - no in-memory state survives a reboot) and one
#     human line to scratch/_guard_parts_watch.log; the report lands at
#     scratch/_guard_parts_report_latest.txt (overwritten nightly -
#     the latest is the one the operator reads).
#   - The push mirrors bs._send_ntfy's wire (topic from push_topic.txt,
#     stdlib-only via powershell Invoke-RestMethod; no heavy imports).
#   - The phone push is HIGH priority ONLY on a tripwire breach; the
#     normal evening trajectory is a default-priority line.

$ErrorActionPreference = 'Continue'
$scout = 'C:\Users\birds\Workspace\kraken_scout'
$hist  = Join-Path $scout 'scratch\_guard_parts_history.jsonl'
$wlog  = Join-Path $scout 'scratch\_guard_parts_watch.log'
$report = Join-Path $scout 'scratch\_guard_parts_report_latest.txt'
$topic = Join-Path $scout 'push_topic.txt'

function Stamp { (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss') }

function Log($msg) {
    Add-Content -Path $wlog -Value "$(Stamp) $msg"
}

Log "watch up (pid $PID)"

$today = (Get-Date -Format 'yyyy-MM-dd')

# ── the readers (each guarded; a failure = a null part, never a throw)
function ReadJson($path) {
    try {
        if (-not (Test-Path $path)) { return $null }
        Get-Content -Raw -Path $path | ConvertFrom-Json
    } catch { $null }
}

$pg   = ReadJson (Join-Path $scout 'profit_guard_state.json')
$ml   = ReadJson (Join-Path $scout 'meta_layers_state.json')
$dc   = ReadJson (Join-Path $scout 'discipline_state.json')

$parts     = $pg.parts
$factor    = $pg.factor
$disc_mod  = $parts.disc_mod
$mlt_corr  = $parts.mlt_corr
$env       = $parts.env
$penalty   = $ml.discipline_penalty_extra
$sal_viol  = $ml.sal_last.violations
$sal_date  = $ml.sal_last.date
$rcl_verdict = $ml.rcl_last.verdict

# the burial share: today's DAY row's system_burial vs total_left
$dayrow = $null
$burial_share = $null
$day_left = $null
$day_sys = $null
if ($dc.history) {
    $dayrow = @($dc.history | Where-Object { $_.date -eq $today -and $_.rot -eq 'DAY' }) | Select-Object -Last 1
    if ($dayrow) {
        $day_left = $dayrow.total_left
        $day_sys  = $dayrow.system_left
        if ($day_left) { $burial_share = [math]::Round($day_sys / $day_left, 3) }
    }
}

# ── the projection: the x0.9 decay per clean day (the sal channel's
#    actual cooldown; a clean SAL close today = violations 0)
$forgiven = 0.0
$forgAVED = $ml.silent_forgaved
if ($forgAVED -and $forgAVED.date -eq $today) { $forgiven = $forgAVED.forgiven }

$proj = @()
foreach ($n in 1..4) {
    if ($penalty) {
        $proj += ([math]::Round($penalty * [math]::Pow(0.9, $n), 3))
    } else {
        $proj += $null
    }
}
function ProjN($n) {
    $i = [int]$n
    if ($i -ge 1 -and $i -le 4) { return $proj[$i - 1] }
    return $null
}

# ── the tripwires (Oct 10 = evening_of_day+2, Oct 11 = +3):
$d10 = [datetime]::Parse('2026-10-10').Date
$today_date = [datetime]::Today
$days_to_10 = ($d10 - $today_date).Days
$days_to_11 = (($d10.AddDays(1)) - $today_date).Days

$trip10 = $false; $trip11 = $false; $trip_why = @()
# Oct 10: the dock's lesson has not decayed to its own per-rotation cap
# by the tripwire date -> the lesson arrived, the clock did not move it
$_p10 = ProjN ([math]::Max($days_to_10, 1))
if (($null -ne $penalty) -and ($_p10 -gt 2.0)) {
    $trip10 = $true
    $trip_why += "Penalty $penalty projects $_p10 on Oct 10 (still above the 2.0 per-rotation cap - the lesson arrived, the decay did not move it by the window)"
}
# Oct 11: disc_mod still under 0.6 after the recovery window ran ->
# the protection dial still dominates the book
$dm_oct11 = $disc_mod
if (($null -ne $disc_mod) -and ($null -ne $penalty) -and ($penalty -gt 0)) {
    # disc_mod moves UP as the penalty decays: each x0.9 on the extra
    # lifts the mod by delta_extra/10. THE PROJECTION'S CONTRACT (the
    # Copilot medium, named on the report): this is the CLEAN-CLOSES
    # projection - the real decay only lands when the SAL closes run
    # clean (violations 0). A violation-heavy stretch means the
    # projection is the CEILING, never the floor - named in the report.
    $dm_oct11 = [math]::Round($disc_mod + (1 - [math]::Pow(0.9, $days_to_11)) * $penalty / 10.0, 3)
    if ($dm_oct11 -lt 0.6) {
        $trip11 = $true
        $trip_why += "disc_mod $disc_mod projects $dm_oct11 on Oct 11 (still under the 0.6 neutral line - the dial still dominates)"
    }
} elseif ($null -ne $disc_mod -and $disc_mod -lt 0.6) {
    # the zero/None-penalty case Copilot caught: a mod already under
    # the line trips REGARDLESS of the penalty being 0 or unreadable
    $trip11 = $true
    $trip_why += "disc_mod $disc_mod already under the 0.6 neutral line (penalty $penalty - the decay projection cannot lift it; the dial still dominates)"
}

# ── the JSONL history row (one per evening, the trajectory reads it)
$row = [ordered]@{
    ts = (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss')
    factor = $factor; disc_mod = $disc_mod; mlt_corr = $mlt_corr
    env = $env; penalty_extra = $penalty; sal_violations = $sal_viol
    sal_date = $sal_date; rcl_verdict = $rcl_verdict
    burial_share = $burial_share; day_left = $day_left
    day_sys = $day_sys; forgiven_today = $forgiven
    proj_1 = (ProjN 1); proj_2 = (ProjN 2); proj_3 = (ProjN 3); proj_4 = (ProjN 4)
    days_to_oct10 = $days_to_10; days_to_oct11 = $days_to_11
    trip_oct10 = $trip10; trip_oct11 = $trip11
}
$row | ConvertTo-Json -Compress | Add-Content -Path $hist

# ── the evening report (the trajectory reads the LAST TWO rows)
$prev_row = $null
if ((Test-Path $hist) -and ((Get-Content $hist).Count -ge 2)) {
    $prev_row = (Get-Content $hist)[-2] | ConvertFrom-Json
}

function Traj($name, $now, $prev) {
    if (($null -eq $prev) -or ($null -eq $now)) { return "$name n/a" }
    $d = [math]::Round(($now - $prev), 3)
    $arrow = if ($d -gt 0) { "up" } elseif ($d -lt 0) { "down" } else { "flat" }
    $s = if ($prev -ne 0 -and ($null -ne $prev)) { [math]::Round((($now - $prev) / [math]::Abs($prev)), 2) } else { 0 }
    return "$name now $now ($arrow $d from yesterday's $prev)"
}

$lines = @()
$lines += "GUARD-PARTS EVENING WATCH - $today (the machine's own read)"
$lines += ""
$lines += "THE PARTS (profit_guard.parts):"
$lines += "  factor      = $factor"
$lines += (Traj 'disc_mod ' $disc_mod ($prev_row.disc_mod))
$lines += (Traj 'mlt_corr ' $mlt_corr ($prev_row.mlt_corr))
$lines += (Traj 'env      ' $env ($prev_row.env))
$lines += ""
$lines += "THE PENALTY CHANNEL (the dock's ledger + the x0.9 decay):"
$lines += (Traj 'penalty  ' $penalty ($prev_row.penalty_extra))
$lines += "  sal_close today: violations $sal_viol (no dock today = clean; the x0.9 held tonight)"
$lines += "  burial_share today: $burial_share (day_sys $day_sys / day_left $day_left)"
$lines += "  forgiveness today: $forgiven"
$lines += ""
$lines += "THE PROJECTION (x0.9 per clean evening, from today's $penalty):"
foreach ($n in 1..4) {
    $dt = [datetime]::Today.AddDays($n).ToString('MM-dd')
    $lines += "  +$n (evening $dt): extra ~ $(ProjN $n)"
}
$lines += ""
$lines += "THE TRIPWIRES:"
$t10dt = [datetime]::Parse('2026-10-10').ToString('MM-dd')
$t11dt = [datetime]::Parse('2026-10-11').ToString('MM-dd')
if ($days_to_10 -le 0) {
    $lines += "  Oct 10 ($t10dt) is TODAY/available: trip10 evaluates NOW"
} else {
    $lines += "  Oct 10 ($t10dt) in $days_to_10 evenings: trip fires if the projection still reads above 2.0 then"
}
if ($days_to_11 -le 0) {
    $lines += "  Oct 11 ($t11dt) is TODAY/available: trip11 evaluates NOW"
} else {
    $lines += "  Oct 11 ($t11dt) in $days_to_11 evenings: trip fires if disc_mod's projection still reads under 0.6 then"
}
if ($trip10 -or $trip11) {
    $lines += ""
    $lines += "TRIPPED:"
    foreach ($t in $trip_why) { $lines += "  * $t" }
} else {
    $lines += ""
    $lines += "tripwires: CLEAR (no breach tonight)"
}
# THE FORGIVENESS STAMP (the audit, the phantom-stamp guard Oct 8): the
# day stamp written by a DRIVE run in the live meta state would
# suppress tonight's REAL forgiveness - the watcher names it (it never
# writes meta state itself)
if ($forgAVED -and $forgAVED.date -eq $today) {
    $lines += ""
    $lines += "THE FORGIVENESS STAMP: rot=$($forgAVED.rot) date=$($forgAVED.date) forgiven=$($forgAVED.forgiven) relief=$($forgAVED.relief_seen)"
}

$report_text = $lines -join "`r`n"
Set-Content -Path $report -Value $report_text -Encoding utf8
$report_text | Add-Content -Path $wlog

# ── the phone push: HIGH on a trip, default otherwise
$prio = if ($trip10 -or $trip11) { 'high' } else { 'default' }
$tag = if ($trip10 -or $trip11) { 'rotating_light' } else { 'chart_with_upwards_trend' }
try {
    $tp = (Get-Content -Raw $topic).Trim()
    if ($tp) {
        $body = "guard parts $today | disc_mod $disc_mod | mlt_corr $mlt_corr | factor $factor | penalty $penalty | proj Oct10 $(ProjN ([math]::Max($days_to_10, 1))) Oct11 dm $dm_oct11"
        if ($trip10 -or $trip11) {
            $body = "TRIP: $body | $($trip_why -join ' ; ')"
        }
        Invoke-RestMethod -Uri "https://ntfy.sh/$tp" -Method Post `
            -Body $body -Headers @{ Title = 'Kraken Scout guard parts'; Priority = $prio; Tags = $tag } | Out-Null
        Log "pushed ($prio)"
    } else {
        Log "push skipped: no topic"
    }
} catch {
    Log "push failed: $_"
}
Log "watch done"
exit 0