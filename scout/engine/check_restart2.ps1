# All python.exe processes unfiltered (restart check follow-up).
$out = "C:\Users\birds\Workspace\kraken_scout\restart_check2.txt"
$lines = @()
$procs = Get-CimInstance Win32_Process -Filter "name='python.exe'" | Sort-Object CreationDate
foreach ($p in $procs) {
    $cmd = $p.CommandLine
    if (-not $cmd) { $cmd = "(no command line)" }
    $lines += ("PID {0}  created {1}  parent {2}  cmd: {3}" -f $p.ProcessId, $p.CreationDate, $p.ParentProcessId, $cmd.Substring(0, [Math]::Min(150, $cmd.Length)))
}
$lines | Out-File -FilePath $out -Encoding ASCII
Write-Output "DONE"
