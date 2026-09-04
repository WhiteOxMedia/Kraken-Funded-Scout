"""Reconstruct TRUMP's shape at the 12:36 alert: where was the high?"""
import sys
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

import ccxt

MST = ZoneInfo("America/Denver")
ex = ccxt.kraken({"enableRateLimit": True})
ohl = ex.fetch_ohlcv("TRUMP/USD", timeframe="1h", limit=24)

print(f"{'candle (MST)':<17} {'open':>9} {'high':>9} {'low':>9} {'close':>9}")
for c in ohl:
    t = datetime.fromtimestamp(c[0] / 1000, MST)
    print(f"{t.strftime('%H:%M'):<17} {c[1]:>9.4f} {c[2]:>9.4f} {c[3]:>9.4f} {c[4]:>9.4f}")

his = [(datetime.fromtimestamp(c[0] / 1000, MST), c[2]) for c in ohl]
hi_t, hi = max(his, key=lambda x: x[1])
fire = MST.localize(datetime.now().replace(hour=12, minute=36, second=0, microsecond=0))
print(f"\n24h HIGH: {hi:.4f} at {hi_t.strftime('%H:%M')} MST")
print(f"alert fired 12:36 — {(fire - hi_t).total_seconds()/60:.0f} minutes after the high")
at_fire = [c for c in ohl if datetime.fromtimestamp(c[0]/1000, MST).hour == 12][0]
print(f"12:00 candle: open {at_fire[1]:.4f} close {at_fire[4]:.4f} — "
      f"price at fire was {(at_fire[4]-hi)/hi*100:+.2f}% vs the high")
