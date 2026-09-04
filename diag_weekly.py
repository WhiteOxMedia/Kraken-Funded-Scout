"""Was each of today's buys near the top of its WEEKLY range? For every
bridge signal today: pull the 7-day range of candles that had CLOSED
before the signal, then place the signal's entry inside that range.
>100% = buying ABOVE the entire prior week (breaking the weekly high).
This is the user's hypothesis: the good call (ARB) was NOT near its weekly
top; the bad ones were. READ-ONLY diagnosis."""
import sys
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
MST = ZoneInfo("America/Denver")

import ccxt
ex = ccxt.kraken()

conn = sqlite3.connect("signals.db")
conn.row_factory = sqlite3.Row
rows = conn.execute("""
    SELECT pair, timestamp, entry_price, confidence, status
    FROM signals
    WHERE action='buy' AND timestamp LIKE '2026-08-31%'
    ORDER BY timestamp
""").fetchall()
conn.close()

VERDICT = {
    "HYPE/USD": "BAD (top, bot holds it)",
    "ARB/USD": "GOOD (+$353 contest)",
    "JUP/USD": "bad (flat, sold)",
    "BTC/USD": "bad (whipsaw out)",
    "POPCAT/USD": "bad (scratch)",
    "CRV/USD": "n/a (bot rejected)",
    "OOB/USD": "n/a (bot rejected)",
    "XMR/USD": "n/a (bot rejected)",
}

print(f"{'PAIR':<12}{'TIME':<7}{'ENTRY':>10}{'7D LOW':>10}{'7D HIGH':>10}"
      f"{'% OF WEEK RANGE':>17}{'VERDICT':>24}")
print("-" * 95)
for r in rows:
    ts = datetime.fromisoformat(r["timestamp"])
    entry = float(r["entry_price"])
    pair = r["pair"]
    try:
        o = ex.fetch_ohlcv(pair, timeframe="4h", limit=50)  # ~8 days
        closed = [c for c in o
                  if datetime.fromtimestamp((c[0] + 4 * 3600 * 1000) / 1000, timezone.utc)
                  <= ts.astimezone(timezone.utc) + timedelta(minutes=5)]
        lo = min(c[3] for c in closed)
        hi = max(c[2] for c in closed)
        pos = (entry - lo) / (hi - lo) * 100 if hi > lo else 50.0
        v = VERDICT.get(pair, "")
        print(f"{pair:<12}{ts.strftime('%H:%M'):<7}{entry:>10.4f}{lo:>10.4f}{hi:>10.4f}"
              f"{pos:>15.0f}%{v:>24}")
    except Exception as e:
        print(f"{pair:<12}{ts.strftime('%H:%M'):<7}{entry:>10.4f}  fetch failed: {e}")
