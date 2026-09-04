"""Why didn't we catch HYPE at 10:00, before the big hike? Reconstruct
the morning: HYPE's candles hour by hour, when the move actually started,
and what the gates said at each point (the tide was OUT until ~10:20 per
this morning's diagnosis). READ-ONLY."""
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
MST = ZoneInfo("America/Denver")

import ccxt
ex = ccxt.kraken()

o = ex.fetch_ohlcv("HYPE/USD", timeframe="15m", limit=96)  # 24h
print("HYPE/USD 15m — the morning timeline (close + % vs 06:00 base):")
base = None
prev = None
for c in o:
    t = datetime.fromtimestamp(c[0] / 1000, MST)
    if t.hour < 6:
        continue
    if base is None:
        base = c[1]
    chg = (c[4] - base) / base * 100
    step = (c[4] - prev) / prev * 100 if prev else 0.0
    prev = c[4]
    flag = "  <<<<" if step >= 1.0 else ""
    # print every candle from 9:00-11:00, hourly summary otherwise
    if 9 <= t.hour <= 11 or t.minute == 0 or step >= 1.0:
        print(f"  {t.strftime('%H:%M')}  C {c[4]:8.4f}  {chg:+6.2f}% vs 06:00  "
              f"candle {step:+5.2f}%{flag}")
print()
tk = ex.fetch_ticker("HYPE/USD")
print(f"24h change: {tk.get('percentage'):.1f}%  last {tk.get('last')}")
