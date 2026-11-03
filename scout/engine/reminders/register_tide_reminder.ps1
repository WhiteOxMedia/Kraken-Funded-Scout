# Registers the one-shot tide-data reminder toast for 2026-09-07 10:00
# (one week after the Aug 31 restart). Task Scheduler fires it whether or
# not Workshop is open; survives reboots; fires at next login/boot if the
# machine was off at the trigger time (StartWhenAvailable).
# Re-run this file to re-schedule (edit the date below first).

$script = "C:\Users\birds\Workspace\kraken_scout\reminders\tide_reminder.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Once -At ([datetime]"2026-09-07 10:00:00")
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "KrakenTideDataReminder" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "Kraken Scout: one week of tide-stamped trades is in - run diag_tidedata.py (user reminder, Aug 31)" -Force

Get-ScheduledTaskInfo -TaskName "KrakenTideDataReminder" |
    Select-Object TaskName, NextRunTime, LastRunTime
