' Kraken Scout - windowless launcher for the GUARD-PARTS night watch
' (Oct 8). The scheduled task runs THIS (wscript = GUI subsystem, no
' console window), and the VBS runs the PowerShell watcher hidden -
' the extension tripwire's launcher idiom.
CreateObject("WScript.Shell").Run _
    "powershell -NoProfile -ExecutionPolicy Bypass -File ""C:\Users\birds\Workspace\kraken_scout\reminders\guard_parts_watch.ps1""", _
    0, False
