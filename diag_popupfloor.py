"""Popup-floor calibration (user rule, Sep 4: "the popup floor 89% doesn't
know about the rotation schedule — it needs to self-adjust accordingly").

The flat 89 popup floor predates the rotation schedule. This diag rebuilds
the evidence the rotation rails sit on: every executed buy in signals.db
with a decided outcome (closed, pnl != 0), bucketed by Denver rotation ×
confidence band — win rate, count, net $ per cell. The same method that
calibrated the tide floor (diag_tidehistory.py: in RISING tides ≥89 picks
won 48% while sub-60 won 83%).

Run: .venv\\Scripts\\python.exe diag_popupfloor.py  →  _popupfloor_check.txt
"""
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

import background_scanner as bs

MST = ZoneInfo("America/Denver")
ROT_KEYS = ["00-04", "04-08", "08-12", "12-16", "16-20", "20-24"]
BANDS = ["<60", "60-69", "70-78", "79-88", "89+"]

lines = []


def out(s=""):
    print(s)
    lines.append(s)


def band(c):
    c = float(c or 0)
    if c < 60:
        return "<60"
    if c < 70:
        return "60-69"
    if c < 79:
        return "70-78"
    if c < 89:
        return "79-88"
    return "89+"


def rot_key(hour):
    return ROT_KEYS[min(hour // 4, 5)]


# ── load executed buys with decided outcomes ─────────────────────────
sc = sqlite3.connect("signals.db")
sc.row_factory = sqlite3.Row
rows = sc.execute("""
    SELECT pair, confidence, source, executed_at, pnl, status
    FROM signals
    WHERE action='buy' AND executed_at IS NOT NULL
    ORDER BY executed_at
""").fetchall()
sc.close()

trades = []
for r in rows:
    if r["pnl"] is None or r["status"] != "closed":
        continue
    try:
        pnl = float(r["pnl"])
    except (TypeError, ValueError):
        continue
    if pnl == 0:  # rotation reset / open — not an outcome
        continue
    dt = datetime.fromisoformat(r["executed_at"])
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MST)
    dt = dt.astimezone(MST)
    trades.append({"dt": dt, "rot": rot_key(dt.hour), "conf": float(r["confidence"] or 0),
                   "pnl": pnl, "pair": r["pair"], "source": r["source"]})

out(f"executed buys: {len(rows)} | decided outcomes: {len(trades)}")
out()

# ── the evidence table ───────────────────────────────────────────────
buckets = defaultdict(lambda: defaultdict(list))
for t in trades:
    buckets[t["rot"]][band(t["conf"])].append((t["pnl"] > 0, t["pnl"]))


def cell(lst):
    n = len(lst)
    if n == 0:
        return f"{'—':>15}"
    wr = 100 * sum(w for w, _ in lst) / n
    tot = sum(p for _, p in lst)
    return f"{wr:4.0f}% ({n:>3}) {tot:+8.1f}"


out("PER-ROTATION outcome × confidence band   win% (n) net$")
out(f"{'rot':>6} | " + " | ".join(f"{b:>15}" for b in BANDS))
for k in ROT_KEYS:
    out(f"{k:>6} | " + " | ".join(f"{cell(buckets[k][b]):>15}" for b in BANDS))
out()

# ── era split (did the old broken strategy drive it?) ────────────────
cut = datetime(2026, 8, 25, tzinfo=MST)  # falling-knife guard era
old = [t for t in trades if t["dt"] < cut]
new = [t for t in trades if t["dt"] >= cut]
for label, grp in [("OLD era (< Aug 25)", old), ("NEW era (>= Aug 25)", new)]:
    n = len(grp)
    w = sum(1 for t in grp if t["pnl"] > 0)
    out(f"{label}: {n} decided, {100 * w / max(n, 1):.0f}% win, net {sum(t['pnl'] for t in grp):+.1f}")
out()

# ── what the rotation rails do to the historical record ──────────────
out("WHAT THE RAILS CHANGE (flat 89 → rotation rail, challenger window ±5):")
for k in ROT_KEYS:
    rail = bs.ROTATION_POPUP_FLOOR[k]
    # blocked now that weren't before (rail > 89): reads in [89, rail)
    newly_blocked = [t for t in trades if t["rot"] == k
                     and 89 <= t["conf"] < rail and t["pnl"] > 0]
    newly_blocked_pnl = sum(t["pnl"] for t in trades if t["rot"] == k
                            and 89 <= t["conf"] < rail)
    # opened now (rail < 89 + challenger): reads in [rail-5, 89)
    opened = [t for t in trades if t["rot"] == k and rail - 5 <= t["conf"] < 89]
    opened_w = sum(1 for t in opened if t["pnl"] > 0)
    opened_pnl = sum(t["pnl"] for t in opened)
    out(f"  {k} rail {rail}: "
        + (f"blocks {len(newly_blocked)} old winners (${newly_blocked_pnl:+.1f} net on that slice)"
           if rail > 89 else "blocks none beyond the old 89")
        + " | "
        + (f"challenger window opens {len(opened)} reads ({opened_w}/{len(opened)} won, "
           f"${opened_pnl:+.1f} net)" if opened else "challenger window opens nothing"))
out()

# ── the rails in force ───────────────────────────────────────────────
out("ROTATION_POPUP_FLOOR in force (Denver rotation → popup floor, FLAT tide):")
for k in ROT_KEYS:
    fl = bs.popup_floor_for_rotation(k)
    out(f"  {k}: {fl}   (RISING tide → {bs.popup_floor('RISING', 0, k):.0f}; "
        f"challenger window ≥ {fl - bs.POPUP_NEAR_MISS_PTS:.0f})")

with open("_popupfloor_check.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print("\n→ _popupfloor_check.txt")
