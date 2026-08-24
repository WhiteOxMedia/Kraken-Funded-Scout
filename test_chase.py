"""Verify chase-guard math on the two real cases + live prices."""
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")
lines = [f"now: {datetime.now(MST).strftime('%a %I:%M %p')}"]

import ccxt
ex = ccxt.kraken()

def chase(pair, ref_price, label):
    ohlcv = ex.fetch_ohlcv(pair, "15m", limit=8)
    lows = [c[3] for c in ohlcv]
    base = min(lows[-4:])  # last 4 x 15m = 1 hour
    px = ohlcv[-1][4]
    ch = (px - base) / base * 100
    lines.append(f"{label}: live {px:.5f} vs 1h base {base:.5f} -> chase +{ch:.1f}% "
                 f"({'REJECT' if ch > 2.0 else 'PASS'})")
    return px

# PUMP: watch entry 0.004673 ($150 = +3.0%)
p = chase("PUMP/USD", 0.004673, "PUMP")
lines.append(f"  vs watch entry 0.004673: {(p-0.004673)/0.004673:+.2%} "
             f"(${(p-0.004673)/0.004673*5000:+.0f} on $5k)")

# PENGU: user entry ~0.00996 top (-$40 = -0.8%)
g = chase("PENGU/USD", 0.00996, "PENGU")
lines.append(f"  vs top entry ~0.00996: {(g-0.00996)/0.00996:+.2%} "
             f"(${(g-0.00996)/0.00996*5000:+.0f} on $5k)")

out = BASE / "chase_check.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
