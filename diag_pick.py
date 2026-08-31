"""Pick post-mortem: the full trail for one pair — bridge rows, her words,
price action after the signal, and the gate context. Usage:
    python diag_pick.py ARB
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sqlite3
import json
from datetime import datetime
from zoneinfo import ZoneInfo

PAIR = (sys.argv[1] if len(sys.argv) > 1 else "ARB").upper()
MST = ZoneInfo("America/Denver")
now = datetime.now(MST)
print(f"=== {PAIR} post-mortem | now {now.strftime('%Y-%m-%d %H:%M:%S')} ===")
print()

conn = sqlite3.connect("signals.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT * FROM signals WHERE pair LIKE ? ORDER BY timestamp DESC LIMIT 5",
    (PAIR + "%",),
).fetchall()
print(f"1. {PAIR} BRIDGE ROWS ({len(rows)}):")
for r in rows:
    d = dict(r)
    print(f"  {d.get('timestamp','')[:16]}  {d.get('action')}  conf {d.get('confidence')}  "
          f"src {d.get('source')}  status {d.get('status')}")
    print(f"      entry {d.get('entry_price')}  SL {d.get('stop_loss')}  TP {d.get('take_profit')}  "
          f"tide {d.get('tide_state')}/{str(d.get('tide_pct'))[:5]}")
    if d.get("reason"):
        print(f"      reason: {str(d['reason'])[:150]}")
    if d.get("execution_reason"):
        print(f"      outcome: {str(d['execution_reason'])[:150]}")
conn.close()
print()

try:
    vl = [json.loads(l) for l in open("voice_log.jsonl", encoding="utf-8") if l.strip()]
    hits = [d for d in vl if PAIR in str(d.get("text", ""))]
    print(f"2. HER WORDS mentioning {PAIR} ({len(hits)}, last 8):")
    for d in hits[-8:]:
        print(f"  {str(d.get('ts',''))[:16]}  {str(d.get('text',''))[:130]}")
except Exception as e:
    print("2. voice_log read failed:", e)
print()

try:
    import ccxt
    ex = ccxt.kraken()
    o = ex.fetch_ohlcv(f"{PAIR}/USD", timeframe="15m", limit=48)
    if o:
        print(f"3. {PAIR}/USD last 12h (15m):")
        first = o[0][4]
        closes = [c[4] for c in o]
        print(f"   12h open {first:.4f}  high {max(closes):.4f}  low {min(closes):.4f}  "
              f"last {closes[-1]:.4f}  ({(closes[-1]/first-1)*100:+.2f}%)")
        for c in o[-8:]:
            t = datetime.fromtimestamp(c[0] / 1000, MST).strftime("%H:%M")
            print(f"   {t}  O {c[1]:.4f}  H {c[2]:.4f}  L {c[3]:.4f}  C {c[4]:.4f}")
    tk = ex.fetch_ticker(f"{PAIR}/USD")
    print(f"   ticker: last {tk.get('last')}  24h pct {tk.get('percentage')}")
except Exception as e:
    print("3. price fetch failed:", e)
