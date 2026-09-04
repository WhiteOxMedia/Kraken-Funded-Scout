Get-ChildItem 'C:\Users\birds\AppData\Local\Temp' -Directory -Filter 'workshop_*' |
  ForEach-Object {
    Get-ChildItem $_.FullName -Recurse -Filter 'workshop_01M0R7EK1JBE6218_0.txt' -ErrorAction SilentlyContinue
  } | ForEach-Object {
    $_.FullName | Out-File -FilePath 'C:\Users\birds\Workspace\kraken_scout\logpath.txt' -Encoding utf8
    Get-Content $_.FullName | Where-Object { $_ -match 'ZEC|Champion|CHALLENGE|BEST PICK' } | Select-Object -Last 20 |
      Out-File -FilePath 'C:\Users\birds\Workspace\kraken_scout\zec_log.txt' -Encoding utf8
  }
