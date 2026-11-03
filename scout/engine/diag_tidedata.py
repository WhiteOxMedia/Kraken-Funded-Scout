"""TIDE DATA collector (user rule, Aug 31: "collect the data first" before
picking a FLAT sizing number).

The scanner stamps the fleet state (tide_state/tide_pct) on every bridge
signal it writes; the bot executes and the bridge records the outcome (pnl).
This diag aggregates: per tide bucket — trades, win rate, total/avg P&L.

Run: .venv\\Scripts\\python.exe diag_tidedata.py   → _tidedata_check.txt
Judge when a bucket has >= 5 CLOSED trades (open trades don't count — no
outcome yet). pnl=0 rows can be rotation resets (the scanner's block-rotation
close zeroes pnl on non-kept positions) — the per-trade table shows them.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

DB = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "signals.db"

lines = []


def out(s=""):
    print(s)
    lines.append(s)


if not DB.exists():
    out("no signals DB at %s — nothing collected yet" % DB)
    scratch_space.report_temp('_tidedata_check.txt').write_text("\n".join(lines), encoding="utf-8")
    sys.exit(0)

# Ensure the tide columns exist (additive migration — also upgrades a live
# pre-restart DB so the first stamped write lands cleanly).
import signal_bridge as sb
import scratch_space   # scratch policy: nothing volatile lands in the repo root
sb.DB_PATH = DB
sb.init_db()

conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row

rows = conn.execute("""
    SELECT pair, tide_state, tide_pct, confidence, executed_at, closed_at,
           pnl, status, execution_reason
    FROM signals
    WHERE action='buy' AND executed_at IS NOT NULL
      AND tide_state IS NOT NULL AND tide_state != ''
    ORDER BY executed_at
""").fetchall()
conn.close()

if not rows:
    out("TIDE DATA: no stamped trades yet.")
    out("(Stamps start after the scanner restart with the Aug 31 change —")
    out(" then one row per executed scout buy.)")
    scratch_space.report_temp('_tidedata_check.txt').write_text("\n".join(lines), encoding="utf-8")
    sys.exit(0)

out("=" * 72)
out("TIDE DATA — FLAT vs RISING outcomes (the sizing-factor decision record)")
out("=" * 72)

buckets = {}
for r in rows:
    b = buckets.setdefault(str(r["tide_state"]), {"trades": 0, "wins": 0, "losses": 0,
                                                  "zeros": 0, "open": 0,
                                                  "pnl": 0.0, "closed": 0})
    b["trades"] += 1
    if r["status"] != "closed":
        b["open"] += 1
        continue
    b["closed"] += 1
    pnl = float(r["pnl"] or 0)
    b["pnl"] += pnl
    if pnl > 0:
        b["wins"] += 1
    elif pnl < 0:
        b["losses"] += 1
    else:
        b["zeros"] += 1

out(f"{'tide':8} {'trades':>6} {'closed':>6} {'open':>5} {'W':>3} {'L':>3} {'0':>3} "
    f"{'win%':>6} {'total$':>9} {'avg$':>8}")
for state in ("RISING", "FLAT", "OUT", "UNKNOWN"):
    if state not in buckets:
        continue
    b = buckets[state]
    decided = b["wins"] + b["losses"]
    winpct = 100.0 * b["wins"] / decided if decided else 0.0
    avg = b["pnl"] / b["closed"] if b["closed"] else 0.0
    out(f"{state:8} {b['trades']:>6} {b['closed']:>6} {b['open']:>5} "
        f"{b['wins']:>3} {b['losses']:>3} {b['zeros']:>3} "
        f"{winpct:>5.0f}% {b['pnl']:>9.2f} {avg:>8.2f}")

out()
out("W/L = closed trades with pnl > 0 / < 0.  0 = pnl exactly $0 (possible")
out("rotation reset, not a real outcome).  open = still held, no outcome yet.")
out()

out("PER-TRADE (newest last):")
out(f"{'executed_at (MST)':22} {'pair':10} {'tide':8} {'fleet%':>6} {'conf':>5} {'pnl':>9}  status")
for r in rows:
    pnl = f"{float(r['pnl']):.2f}" if r["pnl"] is not None else "—"
    out(f"{str(r['executed_at'])[:19]:22} {str(r['pair'])[:10]:10} "
        f"{str(r['tide_state']):8} {float(r['tide_pct'] or 0):>6.0f} "
        f"{float(r['confidence'] or 0):>5.0f} {pnl:>9}  {r['status']}")

out()
readiness = [f"{s}: {buckets[s]['closed']} closed" for s in buckets]
out(f"READINESS: {', '.join(readiness)} (judge a bucket at >= 5 closed)")
out("DECISION FRAME: if FLAT win rate sits well below RISING's (>= 15pts")
out("gap) and avg P&L is negative, a FLAT discount is justified — pick the")
out("number then. Otherwise keep 99% in every tradeable tide.")

scratch_space.report_temp('_tidedata_check.txt').write_text("\n".join(lines), encoding="utf-8")
print("DONE")
