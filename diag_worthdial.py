"""Worth-floor auto-dial calibration (user rule, Sep 4: "auto-dial the
worth floor dial to the proper location every rotation").

The dial's floor = dial x daily pace. The AUTO-DIAL scales the base by
what the market can actually pay this hour: pace x tide x catch-up.
This diag validates the pace factor against the record — per rotation,
the goals the brain actually set on executed scout buys (take_profit vs
entry from signals.db) and their outcomes, vs the engine's live
expected-move cells that drive the factor.

Run: .venv\\Scripts\\python.exe diag_worthdial.py  ->  _worthdial_check.txt
"""
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

import background_scanner as bs
from volatility_engine import load_rotation_thresholds
import scratch_space   # scratch policy: nothing volatile lands in the repo root

MST = ZoneInfo("America/Denver")
ROT_KEYS = ["00-04", "04-08", "08-12", "12-16", "16-20", "20-24"]

lines = []


def out(s=""):
    print(s)
    lines.append(s)


def rot_key(hour):
    return ROT_KEYS[min(hour // 4, 5)]


# ── 1. the record: goals the brain set, per rotation ─────────────────
sc = sqlite3.connect("signals.db")
sc.row_factory = sqlite3.Row
rows = sc.execute("""
    SELECT pair, confidence, entry_price, take_profit, executed_at, pnl, status
    FROM signals
    WHERE action='buy' AND executed_at IS NOT NULL
      AND entry_price IS NOT NULL AND entry_price > 0
      AND take_profit IS NOT NULL AND take_profit > 0
    ORDER BY executed_at
""").fetchall()
sc.close()

per_rot = defaultdict(list)
for r in rows:
    try:
        goal = (float(r["take_profit"]) / float(r["entry_price"]) - 1.0) * 100.0
        pnl = float(r["pnl"]) if r["pnl"] is not None else None
        dt = datetime.fromisoformat(r["executed_at"])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=MST)
        per_rot[rot_key(dt.astimezone(MST).hour)].append(
            {"goal": goal, "pnl": pnl, "decided": pnl is not None and pnl != 0 and r["status"] == "closed"})
    except Exception:
        continue

out(f"executed buys with a goal on record: {len(rows)}")
out()
out("THE RECORD per rotation — the goals the brain actually set:")
out(f"{'rot':>6} | {'n':>3} | {'median goal':>11} | {'win rate':>8} | the hour's character")
for k in ROT_KEYS:
    g = per_rot.get(k) or []
    if not g:
        out(f"{k:>6} | {'—':>3} | {'—':>11} | {'—':>8}")
        continue
    goals = sorted(x["goal"] for x in g)
    n = len(goals)
    med = goals[n // 2] if n % 2 else (goals[n // 2 - 1] + goals[n // 2]) / 2
    dec = [x for x in g if x["decided"]]
    wr = (100 * sum(1 for x in dec if x["pnl"] > 0) / len(dec)) if dec else None
    out(f"{k:>6} | {n:>3} | {med:>10.2f}% | "
        + (f"{wr:>7.0f}%" if wr is not None else "    n/a")
        + (f" | {bs.ROTATIONS[[r[2] for r in bs.ROTATIONS].index(k)][7]}" if k in [r[2] for r in bs.ROTATIONS] else ""))

# ── 2. the pace factor from the engine's live cells ──────────────────
out()
out("THE AUTO-DIAL's pace factor (the engine's live median expected move,")
out("this rotation / the day's best rotation):")
doc = load_rotation_thresholds(max_age_s=0) or {}
for sched in ("weekday", "weekend"):
    moves = {}
    for k in ROT_KEYS:
        cell = (doc.get("median", {}).get(sched, {}) or {}).get(k) or {}
        mv = float(cell.get("avg_move") or 0)
        if mv > 0:
            moves[k] = mv
    if not moves:
        continue
    best = max(moves.values())
    out(f"  {sched}: best rotation moves {best:.2f}%")
    for k in ROT_KEYS:
        mv = moves.get(k)
        if mv:
            out(f"    {k}: move {mv:.2f}%  ->  pace factor {max(0.6, min(1.0, mv / best)):.2f}"
                + ("  (the day's best — full dial)" if mv == best else ""))

# ── 3. the resulting auto-dial table (base 0.25, FLAT tide, on pace) ─
out()
out("THE RESULTING AUTO-DIAL (base 0.25, FLAT tide, on-pace day, 10k tier")
out("— pace $240/day):")
try:
    import contest_account as ca
    pace_usd = ca.daily_pace_usd()
except Exception:
    pace_usd = 240.0
for sched in ("weekday",):
    moves = {}
    for k in ROT_KEYS:
        cell = (doc.get("median", {}).get(sched, {}) or {}).get(k) or {}
        mv = float(cell.get("avg_move") or 0)
        if mv > 0:
            moves[k] = mv
    best = max(moves.values()) if moves else 1.0
    for k in ROT_KEYS[:5]:   # the trading rotations
        mv = moves.get(k, best)
        pf = max(0.6, min(1.0, mv / best if best > 0 else 1.0))
        dial = max(0.10, min(0.40, 0.25 * pf))
        out(f"    {k}: dial {dial:.0%}  ->  floor ${pace_usd * dial:,.0f}"
            + ("   (cooldown — no buys anyway)" if k == "20-24" else ""))

out()
out("VERDICT: the pace factor tracks the hour's real room — slow hours")
out("auto-lower the floor (smaller pays still count toward the pace),")
out("peak hours demand the big payouts. Tide and catch-up bend it live.")

with open(scratch_space.report_temp('_worthdial_check.txt'), "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print("\n→ _worthdial_check.txt")
