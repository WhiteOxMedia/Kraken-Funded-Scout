"""PENGU 100% signal forensics: when, why, what happened since."""
import sys, sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")
now = datetime.now(MST)
lines = [f"now: {now.strftime('%a %I:%M %p')} Denver", ""]

# ── 1. bridge records for PENGU today ──────────────────────────────
conn = sqlite3.connect(BASE / "signals.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT timestamp, pair, action, status, source, confidence, entry_price, stop_loss, take_profit, reason, execution_reason "
    "FROM signals WHERE pair LIKE '%PENGU%' AND timestamp >= '2026-08-24T12:00' ORDER BY timestamp").fetchall()
lines.append(f"bridge PENGU records since noon: {len(rows)}")
for r in rows:
    lines.append(f"  {r['timestamp'][:19]} {r['action']} {r['status']:<9} src={r['source']:<13} "
                 f"conf={r['confidence']:.0f}")
    lines.append(f"     entry={r['entry_price']} stop={r['stop_loss']} tp={r['take_profit']}")
    if r["reason"]:
        lines.append(f"     reason: {r['reason'][:160]}")
    if r["execution_reason"]:
        lines.append(f"     exec: {r['execution_reason'][:120]}")
conn.close()

# ── 2. tournament winners PENGU today ──────────────────────────────
try:
    import db as scout_db
    with scout_db.get_session() as s:
        ws = scout_db.get_tournament_winners(limit=60)
        for w in ws:
            t = w.timestamp
            if t.tzinfo is None:
                t = t.replace(tzinfo=ZoneInfo("UTC"))
            t = t.astimezone(MST)
            if "PENGU" in (w.pair or "") and t.date() == now.date():
                lines.append(f"winner {t.strftime('%H:%M')} {w.pair} {w.action} {w.confidence:.0f}% "
                             f"reasons: {'; '.join((w.reasons or [])[:4])[:220]}")
except Exception as e:
    lines.append(f"winners query failed: {e}")

# ── 3. live price + pace data ──────────────────────────────────────
import ccxt
ex = ccxt.kraken()
t = ex.fetch_ticker("PENGU/USD")
lines.append("")
lines.append(f"PENGU live: {t['last']}  (24h change {t.get('percentage', 0):+.1f}%)")
ohlcv = ex.fetch_ohlcv("PENGU/USD", "15m", limit=8)
for c in ohlcv:
    dt = datetime.fromtimestamp(c[0] / 1000, MST)
    lines.append(f"  {dt.strftime('%H:%M')}  O {c[1]:.5f}  H {c[2]:.5f}  L {c[3]:.5f}  C {c[4]:.5f}")

# engine pace for PENGU
try:
    from background_scanner import rotation_key_for_hour, _schedule_for_dow
    from volatility_engine import load_rotation_thresholds, SCALP_TARGET_PCT
    doc = load_rotation_thresholds(max_age_s=900)
    rot, sched = rotation_key_for_hour(now.hour), _schedule_for_dow(now.weekday())
    cell = doc.get("universe", {}).get("PENGU/USD", {}).get(sched, {}).get(rot) or {}
    avg = float(cell.get("avg_move") or 0)
    lines.append("")
    lines.append(f"engine {rot} {sched}: avg_move={avg:.2f}%  $20-ETA={(SCALP_TARGET_PCT/avg*4 if avg else 0):.1f}h  "
                 f"false_breakout={float(cell.get('false_breakout_rate') or 0):.0%}  reliable={cell.get('reliable')}")
except Exception as e:
    lines.append(f"engine lookup failed: {e}")

out = BASE / "pengu_trace.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
