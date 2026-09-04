$ErrorActionPreference = "SilentlyContinue"
Write-Output "=== ALL python/streamlit processes (full cmdline) ==="
Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match "python|streamlit" -or ($_.CommandLine -and $_.CommandLine -match "python|streamlit")
} | ForEach-Object {
    $cl = $_.CommandLine -replace "\s+", " "
    if ($cl.Length -gt 200) { $cl = $cl.Substring(0, 200) }
    Write-Output ("PID {0,-7} PARENT {1,-7} START {2:MM/dd HH:mm:ss}  {3}" -f $_.ProcessId, $_.ParentProcessId, $_.CreationDate, $cl)
}
Write-Output ""
Write-Output "=== cmd windows tied to workspace ==="
Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "cmd.exe" -and $_.CommandLine -and $_.CommandLine -match "Workspace"
} | ForEach-Object {
    $cl = $_.CommandLine -replace "\s+", " "
    if ($cl.Length -gt 200) { $cl = $cl.Substring(0, 200) }
    Write-Output ("PID {0,-7} PARENT {1,-7} START {2:MM/dd HH:mm:ss}  {3}" -f $_.ProcessId, $_.ParentProcessId, $_.CreationDate, $cl)
}
