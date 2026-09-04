"""TRUMP live status: PnL vs entry, the 1h knife (EMA9/EMA21), and the
window math — the three numbers that decide hold/exit."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import ccxt
import pandas as pd

ex = ccxt.kraken({"enableRateLimit": True})
PAIR = "TRUMP/USD"

w = json.loads(Path("contest_watch.json").read_text())
e = w.get(PAIR) or {}
entry = float(e.get("entry") or 0)
size = float(e.get("size") or 0)
goal = float(e.get("goal_pct") or 3.0)

live = float(ex.fetch_ticker(PAIR)["last"])
pnl_pct = (live - entry) / entry * 100 if entry else 0
pnl_usd = size * pnl_pct / 100 if size else 0
print(f"entry {entry}  live {live}")
print(f"PnL: {pnl_pct:+.2f}%  = ${pnl_usd:+.2f} on ${size:,.0f}  (goal +{goal:.1f}% = ${size*goal/100:,.2f})")

ohl = ex.fetch_ohlcv(PAIR, timeframe="1h", limit=60)
df = pd.DataFrame(ohl, columns=["timestamp", "open", "high", "low", "close", "volume"])
from indicators import ema
ema9 = float(ema(df["close"], 9).iloc[-1])
ema21 = float(ema(df["close"], 21).iloc[-1])
knife = ema9 < ema21 and live < ema21
print(f"\n1h knife: EMA9 {ema9:.4f} {'<' if ema9 < ema21 else '>'} EMA21 {ema21:.4f}, "
      f"live {'below' if live < ema21 else 'above'} EMA21")
print(f"KNIFE: {'FLIPPED — GET OUT territory' if knife else 'not flipped — the trend still holds'}")

# distance to the flip: how far EMA9 sits above EMA21
gap = (ema9 - ema21) / ema21 * 100
print(f"EMA9 gap over EMA21: {gap:+.2f}% (the knife flips when this crosses 0 with price under EMA21)")
