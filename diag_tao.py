"""TAO forensics: was it watched, what did it do, did the light miss it?"""
import sys, json, sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")
now = datetime.now(MST)
lines = [f"now: {now.strftime('%a %I:%M %p')} Denver", ""]

# 1. the watch
try:
    w = json.loads((BASE / "contest_watch.json").read_text())
    lines.append(f"contest watch: {list(w.keys()) or 'EMPTY'}")
    for p, rec in w.items():
        lines.append(f"  {p}: entry={rec.get('entry')} high={rec.get('high')} "
                     f"added={str(rec.get('added'))[:19]}")
    tao = w.get("TAO/USD")
except Exception as e:
    tao = None
    lines.append(f"watch read failed: {e}")

# 2. TAO price action tonight (15m candles)
import ccxt
ex = ccxt.kraken()
oh = ex.fetch_ohlcv("TAO/USD", "15m", limit=16)
lines.append("")
lines.append("TAO 15m candles (last 4 hours):")
for c in oh:
    dt = datetime.fromtimestamp(c[0] / 1000, MST)
    lines.append(f"  {dt.strftime('%H:%M')}  O {c[1]:.2f}  H {c[2]:.2f}  L {c[3]:.2f}  C {c[4]:.2f}")
peak = max(c[2] for c in oh)
last = oh[-1][4]
lines.append(f"4h peak {peak:.2f} -> now {last:.2f} ({(last/peak-1)*100:+.1f}% off peak)")

# 3. bridge records for TAO tonight
conn = sqlite3.connect(BASE / "signals.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT timestamp, pair, action, status, source, confidence, entry_price, execution_reason "
    "FROM signals WHERE pair='TAO/USD' AND timestamp >= '2026-08-25T17:00' ORDER BY timestamp").fetchall()
lines.append("")
lines.append(f"bridge TAO records since 5 PM: {len(rows)}")
for r in rows:
    lines.append(f"  {r['timestamp'][:19]} {r['action']} {r['status']:<9} src={r['source']:<12} "
                 f"conf={r['confidence']:.0f} entry={r['entry_price']} "
                 f"exec={(r['execution_reason'] or '')[:60]}")
conn.close()

# 4. payouts tonight
try:
    tp = json.loads((BASE / "take_payouts.json").read_text())
    lines.append("")
    lines.append(f"take payouts: " + json.dumps(tp))
except Exception as e:
    lines.append(f"payouts read failed: {e}")

out = BASE / "tao_facts.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
