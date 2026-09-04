"""How did PUMP make $60? Verify the run from the user's watch entry."""
import json, sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
MST = ZoneInfo("America/Denver")
lines = [f"now: {datetime.now(MST).strftime('%a %I:%M %p')} Denver"]

w = json.loads((BASE / "contest_watch.json").read_text())
p = w.get("PUMP/USD")
lines.append(f"watch entry: {p['entry']} (added {p['added'][11:16]}), tracked high: {p['high']}, size ${p['size']:,.0f}")
entry = p["entry"]

import ccxt
ex = ccxt.kraken()
ohlcv = ex.fetch_ohlcv("PUMP/USD", "15m", limit=12)
lines.append("")
lines.append("15m candles since the entry:")
for c in ohlcv:
    dt = datetime.fromtimestamp(c[0] / 1000, MST)
    if dt.hour < 14:
        continue
    o, h, l, cl = c[1], c[2], c[3], c[4]
    gain = (h - entry) / entry
    lines.append(f"  {dt.strftime('%H:%M')}  O {o:.5f}  H {h:.5f} (+{gain:.2%} vs entry)  C {cl:.5f}")

t = ex.fetch_ticker("PUMP/USD")
last = t["last"]
lines.append("")
lines.append(f"last: {last:.5f} ({(last-entry)/entry:+.2%} vs entry)")
for usd in (20, 40, 60):
    tgt = entry * (1 + 0.004 * (usd / 20))
    lines.append(f"  +${usd} needed {tgt:.5f} — {'HIT' if p['high'] >= tgt else 'not hit'} (tracked high {p['high']:.5f})")

out = BASE / "pump_60.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
