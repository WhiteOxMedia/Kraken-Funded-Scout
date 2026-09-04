"""TAO at -$109: will it recover? Real episodes, real numbers, no guessing."""
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")
now = datetime.now(MST)

import ccxt
import pandas as pd
from indicators import ema

ENTRY, PEAK, SIZE = 237.7169, 246.7438, 5000.0
ex = ccxt.kraken()
live = float(ex.fetch_ticker("TAO/USD")["last"])
usd = (live / ENTRY - 1.0) * SIZE
peak_ref = max(ENTRY, PEAK)
dip = (peak_ref - live) / peak_ref * 100

lines = [f"now: {now.strftime('%a %I:%M %p')} Denver",
         f"TAO live {live:.2f} | entry {ENTRY:.2f} -> {'+' if usd>=0 else ''}${usd:,.0f} "
         f"({(live/ENTRY-1)*100:+.2f}%)",
         f"drawdown from the peak ({peak_ref:.2f}): {dip:.1f}%", ""]

# ── THE HISTORY: 5 days of 15m candles, episodes of THIS dip size ─────
oh = ex.fetch_ohlcv("TAO/USD", "15m", limit=480)
ts = [c[0] for c in oh]
highs = [c[2] for c in oh]
lows = [c[3] for c in oh]
episodes = []          # (recovered, minutes)
peak_h = highs[0]
ep = None              # (start_i, peak)
for i in range(1, len(oh)):
    if ep is None:
        if highs[i] > peak_h:
            peak_h = highs[i]
        elif peak_h > 0 and (peak_h - lows[i]) / peak_h * 100 >= dip:
            ep = (i, peak_h)
    else:
        if highs[i] >= ep[1]:
            episodes.append((True, (ts[i] - ts[ep[0]]) / 60000.0))
            peak_h = highs[i]
            ep = None
if ep is not None:
    episodes.append((False, None))
if len(episodes) >= 3:
    rec = [m for ok, m in episodes if ok]
    rate = len(rec) / len(episodes)
    med = sorted(rec)[len(rec)//2] if rec else None
    within60 = sum(1 for m in rec if m <= 60)
    lines.append(f"HISTORY (5 days, 15m bars): TAO fell {dip:.1f}%+ from a peak "
                 f"{len(episodes)}x —")
    lines.append(f"  recovered to the old peak: {len(rec)} of {len(episodes)} ({rate:.0%})")
    if med is not None:
        lines.append(f"  median time to recover: {med:.0f} min")
    lines.append(f"  recovered WITHIN ONE HOUR: {within60} of {len(episodes)} "
                 f"({100.0*within60/len(episodes):.0%})")
else:
    lines.append(f"HISTORY: only {len(episodes)} episodes this size in 5 days — too few to call")

# ── context: trend + tide ────────────────────────────────────────────
closes = pd.Series([c[4] for c in oh])
es, el = float(ema(closes, 9).iloc[-1]), float(ema(closes, 21).iloc[-1])
state = "FALLING" if (es < el and live < el) else ("UP" if es > el else "BASING")
lines.append("")
lines.append(f"1h trend: {state} (EMA9 {es:.1f} vs EMA21 {el:.1f}, price {live:.1f})")
from background_scanner import _fleet_tide
tp, st = _fleet_tide()
lines.append(f"tide: {st} ({tp and f'{tp:.0f}%'})" if st else "tide: no data")

# ── the numbers that matter to the answer ────────────────────────────
lines.append("")
lines.append(f"$20 needs live to rise to {ENTRY*1.004:.2f} "
             f"({(ENTRY*1.004/live-1)*100:+.1f}% from here)")
lines.append(f"break-even needs {ENTRY:.2f} ({(ENTRY/live-1)*100:+.1f}%)")
lines.append(f"old peak needs {peak_ref:.2f} ({(peak_ref/live-1)*100:+.1f}%)")

out = BASE / "tao_recover.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
