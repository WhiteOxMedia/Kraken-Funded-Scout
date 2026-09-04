$ErrorActionPreference = "SilentlyContinue"
Write-Output "=== ALL python/cmd processes tied to the stack ==="
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and (
        $_.CommandLine -like "*background_scanner*" -or
        $_.CommandLine -like "*volatility_engine*" -or
        $_.CommandLine -like "*scout_signal_reader*" -or
        $_.CommandLine -like "*bot_engine*" -or
        $_.CommandLine -like "*streamlit*app.py*" -or
        $_.CommandLine -like "*Launch Kraken*"
    )
} | ForEach-Object {
    $cl = $_.CommandLine -replace "\s+", " "
    if ($cl.Length -gt 160) { $cl = $cl.Substring(0, 160) }
    Write-Output ("PID {0,-7} PARENT {1,-7} START {2:MM/dd HH:mm:ss}  {3}" -f $_.ProcessId, $_.ParentProcessId, $_.CreationDate, $cl)
}
