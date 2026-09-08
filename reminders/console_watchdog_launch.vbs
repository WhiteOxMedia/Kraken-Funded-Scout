' Kraken Scout — windowless launcher for the console watchdog (Sep 7).
' The scheduled task runs THIS (wscript = a GUI-subsystem host, so no
' console window is ever created), and the VBS runs PowerShell hidden.
' The old direct-powershell registration flashed a console window on the
' user's desktop every minute — the bug this file retires.
CreateObject("WScript.Shell").Run _
    "powershell -NoProfile -ExecutionPolicy Bypass -File ""C:\Users\birds\workspace\kraken_scout\reminders\console_watchdog.ps1""", _
    0, False
