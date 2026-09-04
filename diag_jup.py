"""JUP incident check (user: 'JUP just now was a bad signal') — the full
trail: bridge rows, her words, the gate context at signal time, and what
price did after."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sqlite3
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

MST = ZoneInfo("America/Denver")
now = datetime.now(MST)
print("NOW:", now.strftime("%Y-%m-%d %H:%M:%S"), "| tide context below")
print()

# ── 1. bridge rows for JUP ────────────────────────────────────────────
conn = sqlite3.connect("signals.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT * FROM signals WHERE pair LIKE 'JUP%' ORDER BY timestamp DESC LIMIT 10"
).fetchall()
print(f"1. JUP BRIDGE ROWS ({len(rows)}):")
for r in rows:
    d = dict(r)
    print(f"  {d.get('timestamp','')[:16]}  {d.get('action')}  conf {d.get('confidence')}  "
          f"src {d.get('source')}  status {d.get('status')}  entry {d.get('entry_price')}  "
          f"tide {d.get('tide_state')}/{d.get('tide_pct')}")
    if d.get("reason"):
        print(f"      reason: {d['reason'][:160]}")
    if d.get("rejection_reason"):
        print(f"      rejection: {d['rejection_reason'][:160]}")
conn.close()
print()

# ── 2. her words about JUP ────────────────────────────────────────────
try:
    vl = [json.loads(l) for l in open("voice_log.jsonl", encoding="utf-8") if l.strip()]
    jup = [d for d in vl if "JUP" in str(d.get("text", ""))]
    print(f"2. HER WORDS mentioning JUP ({len(jup)}):")
    for d in jup[-8:]:
        print(f"  {str(d.get('ts',''))[:16]}  {str(d.get('text',''))[:130]}")
except Exception as e:
    print("2. voice_log read failed:", e)
print()

# ── 3. what JUP did after (1h + 15m candles) ─────────────────────────
try:
    import ccxt
    ex = ccxt.kraken()
    o = ex.fetch_ohlcv("JUP/USD", timeframe="15m", limit=48)  # last 12h
    if o:
        print("3. JUP/USD last 12h (15m closes, newest last):")
        closes = [c[4] for c in o]
        hi, lo = max(closes), min(closes)
        print(f"   first {closes[0]:.4f}  high {hi:.4f}  low {lo:.4f}  last {closes[-1]:.4f}")
        chg = (closes[-1] / closes[0] - 1) * 100
        print(f"   12h change: {chg:+.2f}%")
        # last 6 candles detail
        import datetime as _dt
        for c in o[-6:]:
            t = _dt.datetime.fromtimestamp(c[0] / 1000, MST).strftime("%H:%M")
            print(f"   {t}  O {c[1]:.4f}  H {c[2]:.4f}  L {c[3]:.4f}  C {c[4]:.4f}")
    tk = ex.fetch_ticker("JUP/USD")
    print(f"   ticker: last {tk.get('last')}  24h pct {tk.get('percentage')}")
except Exception as e:
    print("3. price fetch failed:", e)
print()

# ── 4. the tide + bar context right now ──────────────────────────────
try:
    import background_scanner as bs
    pct, state = bs._fleet_tide()
    print(f"4. TIDE NOW: {state} ({pct}% fleet up)")
    print(f"   rotation bar now: {bs.get_time_block(now.hour, now.weekday())[3]}")
except Exception as e:
    print("4. tide read failed:", e)
