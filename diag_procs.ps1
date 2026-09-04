$ErrorActionPreference = "SilentlyContinue"
Write-Output "=== processes with kraken_scout in command line ==="
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and $_.CommandLine -like "*kraken_scout*"
} | ForEach-Object {
    $cl = $_.CommandLine
    if ($cl.Length -gt 220) { $cl = $cl.Substring(0, 220) + "..." }
    Write-Output ("PID {0}  {1}  START {2}" -f $_.ProcessId, $cl, $_.CreationDate)
}
Write-Output ""
Write-Output "=== streamlit processes (any) ==="
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and $_.CommandLine -like "*streamlit*"
} | ForEach-Object {
    $cl = $_.CommandLine
    if ($cl.Length -gt 220) { $cl = $cl.Substring(0, 220) + "..." }
    Write-Output ("PID {0}  {1}  START {2}" -f $_.ProcessId, $cl, $_.CreationDate)
}
