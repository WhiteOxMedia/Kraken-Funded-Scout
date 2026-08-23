Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
  Where-Object { $_.CommandLine -match 'background_scanner|volatility_engine|bot_runner|streamlit|app\.py' } |
  ForEach-Object { "$($_.ProcessId) :: $($_.CommandLine)" } |
  Out-File -FilePath "C:\Users\birds\Workspace\kraken_scout\proc_check.txt" -Encoding utf8
