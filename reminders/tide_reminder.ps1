# Kraken Scout — TIDE DATA reminder toast (user rule, Aug 31: "trigger a
# toast to fire in a week"). Fired by the Windows Task Scheduler task
# "KrakenTideDataReminder" (2026-09-07 10:00, StartWhenAvailable).
# Speaks via the default voice (Zira), shows a persistent popup, then
# deletes its own scheduled task — a true one-shot.

param(
    [int]$Seconds = 3600   # popup lifetime: up to 1 hour or until clicked
)

$title = "Kraken Scout - TIDE DATA READY"
$msg = "One week of tide-stamped trades is in.`r`n`r`nOpen Workshop and say: tide data.`r`nWe will read the FLAT-vs-RISING record and pick the sizing number (or keep 99%)."

# 1) Zira says it (the scanner may be speaking too — this is one line)
try {
    $v = New-Object -ComObject SAPI.SpVoice
    $null = $v.Speak("The tide data is ready. One week of stamped trades is in. Open Workshop and say tide data.")
} catch { }

# 2) Persistent popup — the same always-works pattern as her buy alerts
try {
    $w = New-Object -ComObject WScript.Shell
    $null = $w.Popup($msg, $Seconds, $title, 48)   # exclamation icon + sound
} catch { }

# 3) One-shot: remove the scheduled task so it never fires again
try {
    schtasks /Delete /TN "KrakenTideDataReminder" /F 2>$null | Out-Null
} catch { }
