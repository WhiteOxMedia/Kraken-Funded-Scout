$ErrorActionPreference = "SilentlyContinue"
$out = Get-CimInstance Win32_Process -Filter "name='python.exe'" |
    ForEach-Object { "{0}`t{1}" -f $_.ProcessId, $_.CommandLine }
$out | Out-File -FilePath "C:\Users\birds\Workspace\kraken_scout\procs2.txt" -Encoding utf8
Write-Output "DONE"
