$ErrorActionPreference = "SilentlyContinue"
Write-Output "=== stack processes (component -> start time) ==="
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and (
        $_.CommandLine -like "*volatility_engine*" -or
        $_.CommandLine -like "*background_scanner*" -or
        $_.CommandLine -like "*bot_runner*" -or
        $_.CommandLine -like "*streamlit*app.py*"
    )
} | ForEach-Object {
    $cl = $_.CommandLine -replace "\s+", " "
    if ($cl.Length -gt 130) { $cl = $cl.Substring(0, 130) }
    Write-Output ("PID {0,-7} START {1:MM/dd HH:mm:ss}  {2}" -f $_.ProcessId, $_.CreationDate, $cl)
}
