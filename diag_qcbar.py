"""SHOW ME: does the brain's confidence number (the QC's 60 floor) actually
separate winners from losers? Every EXECUTED trade the bridge has recorded,
bucketed by entry conviction (the deep number — the same number the QC and
the alerts speak). READ-ONLY — this is the evidence, not a gate. Answer the
question: is 60 the right floor, and does a 71 beat a 62?"""
import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

conn = sqlite3.connect("signals.db")
conn.row_factory = sqlite3.Row

rows = conn.execute("""
    SELECT pair, confidence, pnl, executed_at, closed_at, status,
           source, execution_reason
    FROM signals
    WHERE action='buy' AND status='closed' AND executed_at IS NOT NULL
    ORDER BY executed_at
""").fetchall()
conn.close()

print(f"EXECUTED-AND-CLOSED TRADES: {len(rows)}")
print()

BANDS = [
    ("<60 (below the floor — shouldn't exist)", 0, 60),
    ("60-64 (just above the floor)", 60, 65),
    ("65-69", 65, 70),
    ("70-74", 70, 75),
    ("75-79", 75, 80),
    ("80-89 (champion grade)", 80, 90),
    ("90+", 90, 200),
]


def bucket_of(c):
    for name, lo, hi in BANDS:
        if lo <= c < hi:
            return name
    return "?"


stats = {}
for r in rows:
    conf = float(r["confidence"] or 0)
    pnl = float(r["pnl"] or 0)
    b = stats.setdefault(bucket_of(conf), {"n": 0, "w": 0, "l": 0, "z": 0, "pnl": 0.0})
    b["n"] += 1
    b["pnl"] += pnl
    if pnl > 0:
        b["w"] += 1
    elif pnl < 0:
        b["l"] += 1
    else:
        b["z"] += 1  # zero-pnl: often a rotation reset, not a real round trip

print(f"{'CONFIDENCE BAND':<38}{'N':>4}{'W':>4}{'L':>4}{'0$':>4}{'WIN%':>7}{'TOTAL$':>10}{'AVG$':>8}")
print("-" * 81)
for name, lo, hi in BANDS:
    b = stats.get(name)
    if not b:
        continue
    decided = b["w"] + b["l"]
    win = (b["w"] / decided * 100) if decided else 0.0
    avg = b["pnl"] / b["n"] if b["n"] else 0.0
    print(f"{name:<38}{b['n']:>4}{b['w']:>4}{b['l']:>4}{b['z']:>4}"
          f"{win:>6.0f}%{b['pnl']:>10.2f}{avg:>8.2f}")

# the actionable cut: the floor zone vs everything above it
lo_zone = [r for r in rows if 60 <= float(r["confidence"] or 0) < 70]
hi_zone = [r for r in rows if float(r["confidence"] or 0) >= 70]


def zone_summary(name, zs):
    if not zs:
        print(f"{name}: no trades")
        return
    w = sum(1 for r in zs if float(r["pnl"] or 0) > 0)
    l = sum(1 for r in zs if float(r["pnl"] or 0) < 0)
    tot = sum(float(r["pnl"] or 0) for r in zs)
    dec = w + l
    win = (w / dec * 100) if dec else 0.0
    print(f"{name}: n={len(zs)}  W{w}/L{l}  win {win:.0f}%  total ${tot:+.2f}  "
          f"avg ${tot/len(zs):+.2f}")


print()
print("THE QUESTION — does the number separate?")
zone_summary("60-69 (the floor zone — a higher floor would cut these)", lo_zone)
zone_summary("70+   (everything above the floor zone)                ", hi_zone)
print()

print("EVERY TRADE (the record behind the numbers):")
for r in rows:
    conf = float(r["confidence"] or 0)
    pnl = float(r["pnl"] or 0)
    mark = "+" if pnl > 0 else ("-" if pnl < 0 else "0")
    print(f"  {str(r['executed_at'])[:16]}  {str(r['pair']):<12} conf {conf:>5.1f}  "
          f"{mark} ${pnl:>7.2f}  {str(r['source'] or '')[:12]}")
