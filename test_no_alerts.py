"""Why no desktop alerts? Trace every signal since 8:45 through each gate."""
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")

lines = [f"now: {datetime.now(MST).strftime('%a %I:%M %p')}"]

from background_scanner import _hours_to_scalp, _hours_to_goal, _max_resolve_hours

# every signal since the ghost clear, with its gate verdicts
import sqlite3
conn = sqlite3.connect(BASE / "signals.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT timestamp, pair, action, status, source, confidence, entry_price, take_profit "
    "FROM signals WHERE timestamp >= '2026-08-24T08:45' AND action='buy' "
    "ORDER BY timestamp DESC LIMIT 25").fetchall()
conn.close()

lines.append(f"desktop floor: 89% | resolve cap now: {_max_resolve_hours():.1f}h")
lines.append(f"signals since 8:45: {len(rows)}")
lines.append("")

hi = [r for r in rows if r["confidence"] >= 89]
lines.append(f"≥89% (desktop-eligible): {len(hi)}")
for r in hi:
    eta = _hours_to_scalp(r["pair"], r["entry_price"])   # THE GATE: $20 pace
    tp_eta = _hours_to_goal(r["pair"], r["entry_price"], r["take_profit"])  # old, wrong gate
    eta_txt = f"{eta:.1f}h" if eta else "no data"
    verdict = "should POP now (fix works)" if (eta is None or eta <= _max_resolve_hours()) else "still blocked"
    lines.append(f"  {r['timestamp'][11:16]} {r['pair']:<12} {r['confidence']:.0f}% $20 ETA {eta_txt} (old TP ETA {f'{tp_eta:.0f}h' if tp_eta else 'n/a'}) → {verdict} [status={r['status']}]")

lines.append("")
lines.append("sub-89% (logged only, by design):")
sub = [r for r in rows if r["confidence"] < 89]
seen = {}
for r in sub:
    seen.setdefault(r["pair"], []).append(r["confidence"])
for pair, confs in sorted(seen.items()):
    lines.append(f"  {pair:<12} {len(confs)}x, best {max(confs):.0f}%")

out = BASE / "no_alerts_why.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
