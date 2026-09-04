"""APT post-mortem (Sep 1): the user's report — 'the popups that said
100% confident go big were the most shaky, and the get out was too late.'
Pull the alert record (confidence, entry, time), her words around the pick
and the exit, and the price trail. Score on price facts."""
import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
MST = ZoneInfo("America/Denver")
today = datetime.now(MST).strftime("%Y-%m-%d")

# ── 1. today's APT signals ────────────────────────────────────────────
conn = sqlite3.connect("signals.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT pair, timestamp, confidence, entry_price, status, source, "
    "execution_reason, tide_state FROM signals "
    "WHERE pair LIKE 'APT%' AND timestamp >= ? ORDER BY timestamp", (today,)
).fetchall()
print(f"1. APT SIGNALS TODAY ({len(rows)}):")
for r in rows:
    d = dict(r)
    print(f"   {d['timestamp'][:19]}  conf {d['confidence']}  entry {d['entry_price']}  "
          f"src {d['source']}  status {d['status']}  tide {d['tide_state']}")
    if d.get("execution_reason"):
        print(f"      outcome: {str(d['execution_reason'])[:120]}")
conn.close()
print()

# ── 2. her words: the pick + the exit ────────────────────────────────
vl = [json.loads(l) for l in open("voice_log.jsonl", encoding="utf-8") if l.strip()]
tday = [d for d in vl if str(d.get("ts", "")) >= today]
picks = [d for d in tday if any(k in str(d.get("text", "")).lower()
                                for k in ("buy ", "new pick", "go!", "get out", "skip"))]
print(f"2. HER CALLS TODAY (of {len(tday)} lines — picks/exits):")
for d in picks:
    print(f"   {str(d.get('ts',''))[11:16]}  {str(d.get('text',''))[:110]}")
print()

# ── 3. the price trail since the first signal ────────────────────────
if rows:
    import ccxt
    ex = ccxt.kraken()
    sig_t = datetime.fromisoformat(rows[0]["timestamp"])
    entry = float(rows[0]["entry_price"])
    o = ex.fetch_ohlcv("APT/USD", timeframe="15m", limit=100)
    win = [c for c in o
           if sig_t <= datetime.fromtimestamp(c[0] / 1000, MST) <= sig_t + timedelta(hours=3)]
    if win:
        hi = max(c[2] for c in win)
        hi_t = next(datetime.fromtimestamp(c[0] / 1000, MST)
                    for c in win if c[2] == hi)
        lo = min(c[3] for c in win)
        target = entry * 1.004
        hit = next((datetime.fromtimestamp(c[0] / 1000, MST).strftime("%H:%M")
                    for c in win if c[2] >= target), None)
        now_px = float(ex.fetch_ticker("APT/USD").get("last") or 0)
        print(f"3. PRICE FACTS (entry {entry}, 3h window):")
        print(f"   high {hi} at {hi_t.strftime('%H:%M')}  ({(hi/entry-1)*100:+.2f}% vs entry)")
        print(f"   low  {lo}  ({(lo/entry-1)*100:+.2f}%)")
        print(f"   +0.4% (the guideline move) reached: {'YES at ' + hit if hit else 'NO'}")
        print(f"   now {now_px}  ({(now_px/entry-1)*100:+.2f}%)")
        # the giveback math: exit fired when drop-from-high exceeded threshold
        print(f"   giveback read: peak {(hi/entry-1)*100:+.2f}%, exit at ~12:28")
