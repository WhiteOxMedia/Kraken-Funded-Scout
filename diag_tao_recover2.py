"""TAO rebought at the peak (~244.9), now 239.58. Will +2.2% come in an
hour? Episode history from WEEKS of 1h candles in the engine cache."""
import sys, sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")
now = datetime.now(MST)

ENTRY, SIZE = 244.9, 5000.0
import ccxt
ex = ccxt.kraken()
live = float(ex.fetch_ticker("TAO/USD")["last"])
usd = (live / ENTRY - 1.0) * SIZE
need_be = (ENTRY / live - 1) * 100
need_20 = (ENTRY * 1.004 / live - 1) * 100

lines = [f"now: {now.strftime('%a %I:%M %p')} | TAO live {live:.2f} | your rebuy {ENTRY:.2f} "
         f"-> ${usd:,.0f} ({(live/ENTRY-1)*100:+.2f}%)",
         f"break-even needs +{need_be:.2f}% | your $20 needs +{need_20:.2f}%", ""]

# ── episode history from the 1h cache (weeks of data) ───────────────
cc = sqlite3.connect(BASE / "ohlcv_cache.db")
rows = cc.execute("SELECT ts, open, high, low, close FROM ohlcv "
                  "WHERE pair='TAO/USD' ORDER BY ts").fetchall()
cc.close()
n_candles = len(rows)
first_dt = datetime.fromtimestamp(rows[0][0] / 1000, MST) if rows else None
span_days = (now - first_dt).days if first_dt else 0
lines.append(f"cache: {n_candles} x 1h candles spanning {span_days} days")

DIP = 2.9  # TAO's current drawdown from its recent peak
highs = [r[2] for r in rows]
ts = [r[0] for r in rows]
lows = [r[3] for r in rows]
episodes = []          # (recovered, hours)
peak_h = highs[0]
ep = None
for i in range(1, len(rows)):
    if ep is None:
        if highs[i] > peak_h:
            peak_h = highs[i]
        elif peak_h > 0 and (peak_h - lows[i]) / peak_h * 100 >= DIP:
            ep = (i, peak_h)
    else:
        if highs[i] >= ep[1]:
            episodes.append((True, (ts[i] - ts[ep[0]]) / 3600000.0))
            peak_h = highs[i]
            ep = None
if ep is not None:
    episodes.append((False, None))   # still open at data's end

if len(episodes) >= 3:
    rec = [h for ok, h in episodes if ok]
    rate = len(rec) / len(episodes)
    med = sorted(rec)[len(rec)//2] if rec else None
    within_1h = sum(1 for h in rec if h <= 1.0)
    within_4h = sum(1 for h in rec if h <= 4.0)
    within_24h = sum(1 for h in rec if h <= 24.0)
    lines.append(f"episodes where TAO fell {DIP:.1f}%+ off a peak: {len(episodes)} "
                 f"over {span_days} days")
    lines.append(f"  recovered to the old peak: {len(rec)} of {len(episodes)} ({rate:.0%})")
    lines.append(f"  within 1 HOUR:  {within_1h} ({100.0*within_1h/len(episodes):.0%})")
    lines.append(f"  within 4 hours: {within_4h} ({100.0*within_4h/len(episodes):.0%})")
    lines.append(f"  within 24 hours:{within_24h} ({100.0*within_24h/len(episodes):.0%})")
    if med is not None:
        lines.append(f"  median time to full recovery: {med:.1f} hours")
else:
    lines.append(f"only {len(episodes)} episodes of this size — too rare to call")

out = BASE / "tao_recover2.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
