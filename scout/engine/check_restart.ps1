# Restart verification (Sep 4): process start times vs commit times,
# duplicates, port 8501, bot heartbeat, recent scanner speech.
# ASCII only (PS 5.1).
$out = "C:\Users\birds\Workspace\kraken_scout\restart_check.txt"
$lines = @()

# 1. python processes with command lines + creation times
$lines += "=== PYTHON PROCESSES ==="
$procs = Get-CimInstance Win32_Process -Filter "name='python.exe'" | Sort-Object CreationDate
foreach ($p in $procs) {
    $cmd = $p.CommandLine
    if ($cmd -and ($cmd -like "*background_scanner*" -or $cmd -like "*app.py*" -or $cmd -like "*streamlit*" -or $cmd -like "*bot_runner*" -or $cmd -like "*server.py*" -or $cmd -like "*pool*")) {
        $lines += ("PID {0}  created {1}  cmd: {2}" -f $p.ProcessId, $p.CreationDate, $cmd.Substring(0, [Math]::Min(140, $cmd.Length)))
    }
}
$lines += ("total python.exe count: {0}" -f (Get-Process python -ErrorAction SilentlyContinue).Count)

# 2. port 8501 listening?
$lines += ""
$lines += "=== PORT 8501 ==="
$t = netstat -ano | Select-String ":8501.*LISTENING"
if ($t) { $lines += ($t | ForEach-Object { $_.Line.Trim() }) } else { $lines += "NOT LISTENING" }

# 3. bot heartbeat age
$lines += ""
$lines += "=== BOT HEARTBEAT ==="
$hb = "C:\Users\birds\Workspace\kraken_xrp_trading_bot\bot_heartbeat.txt"
if (Test-Path $hb) {
    $age = (Get-Date) - (Get-Item $hb).LastWriteTime
    $lines += ("last write: {0}  (age {1:mm} min {1:ss} s)" -f (Get-Item $hb).LastWriteTime, $age)
} else {
    $lines += "no heartbeat file"
}

# 4. scanner liveness: scanner.lock + voice_log tail
$lines += ""
$lines += "=== SCANNER LOCK / VOICE ==="
$lock = "C:\Users\birds\Workspace\kraken_scout\scanner.lock"
if (Test-Path $lock) {
    $lines += ("scanner.lock last write: {0}" -f (Get-Item $lock).LastWriteTime)
} else { $lines += "no scanner.lock" }
$vl = Get-Content "C:\Users\birds\Workspace\kraken_scout\voice_log.jsonl" -Tail 5 -ErrorAction SilentlyContinue
$lines += "voice_log tail:"
foreach ($l in $vl) { $lines += ("  " + $l) }

# 5. commit timestamps for comparison
$lines += ""
$lines += "=== COMMIT TIMES ==="
$lines += ("bot repo:    " + (git -C "C:\Users\birds\Workspace\kraken_xrp_trading_bot" log -1 --format="%h %ci %s"))
$lines += ("scout repo:  " + (git -C "C:\Users\birds\Workspace\kraken_scout" log -1 --format="%h %ci %s"))

$lines | Out-File -FilePath $out -Encoding ASCII
Write-Output "DONE"
