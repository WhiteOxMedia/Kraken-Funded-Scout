Get-Content 'C:\Users\birds\Workspace\kraken_xrp_trading_bot\logs\bot.log' -Tail 60 |
  Where-Object { $_ -match 'Own scanning|BOUGHT|SOLD|Scout|No open positions|Position #' } |
  Select-Object -Last 15 |
  Out-File -FilePath 'C:\Users\birds\Workspace\kraken_scout\botmode.txt' -Encoding utf8
