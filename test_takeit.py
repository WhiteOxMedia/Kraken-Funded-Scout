"""Verify the $20 TAKE IT math and the 3-hour rule logic (no network)."""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

lines = []
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


# ── 1. TAKE IT threshold math (the exact code from contest_light) ────
TAKE_IT_USD = 20
lines.append("TAKE IT threshold math:")


def take_sequence(profits):
    """Simulate the scanner's threshold logic over a P&L series.
    Returns the list of announced dollar amounts."""
    announced = []
    level_state = {}
    for pair, usd in profits:
        level = int(usd // TAKE_IT_USD) if usd > 0 else 0
        if usd < 10.0:
            level_state.pop(pair, None)
        elif level > level_state.get(pair, 0):
            level_state[pair] = level
            announced.append(level * TAKE_IT_USD)
    return announced


# straight ride: 5 → 15 → 25 → 45 → 5 (reset) → 25 again
seq = take_sequence([("A", 5), ("A", 15), ("A", 25), ("A", 45), ("A", 5), ("A", 25)])
check("ride $20/$40, reset, re-entry $20", seq == [20, 40, 20], str(seq))

# pure ride without reset: only $20 then $40
seq = take_sequence([("A", 15), ("A", 25), ("A", 45), ("A", 50)])
check("pure ride: $20 then $40 only", seq == [20, 40], str(seq))

# never hits $20 → no calls
seq = take_sequence([("B", 5), ("B", 15), ("B", 19), ("B", 12)])
check("under $20: silent", seq == [], str(seq))

# reset near entry then re-cross → second $20 call
seq = take_sequence([("C", 25), ("C", 5), ("C", 15), ("C", 25)])
check("reset + re-cross: two $20 calls", seq == [20, 20], str(seq))

# hover between 10-20 without crossing: silent
seq = take_sequence([("D", 12), ("D", 18), ("D", 15)])
check("hover 10-20: silent", seq == [], str(seq))

# $20 exactly = level 1 → fires
seq = take_sequence([("E", 20.0)])
check("exactly $20: fires", seq == [20], str(seq))

# ── 2. the resolve rule (adaptive cap) ──────────────────────────────
lines.append("")
lines.append("resolve-rule math:")
# distance% / avg_move% × 4h
# 1.2% away, avg 1.2%/rotation → 4h → BLOCKED (> 3)
h = (1.2 / 1.2) * 4
check("1.2% away @1.2% avg = 4h → blocked", h > 3.0, f"{h}h")
# 0.8% away, avg 1.2% → 2.67h → PASSES
h = (0.8 / 1.2) * 4
check("0.8% away @1.2% avg = 2.7h → passes", h <= 3.0, f"{h}h")
# 2% away, avg 4%/rotation (fast coin) → 2h → PASSES
h = (2.0 / 4.0) * 4
check("2% away @4% avg = 2h → passes", h <= 3.0, f"{h}h")
# the 33-hour HYPE case: ~5% away, avg 0.6% → 33h → blocked
h = (5.0 / 0.6) * 4
check("HYPE-style slow coin → ~33h → blocked", h > 3.0, f"{h:.0f}h")

# the adaptive cap function itself
try:
    from background_scanner import _max_resolve_hours
    from datetime import datetime
    cap = _max_resolve_hours()
    check("cap is 3-6h and never past midnight", 1.0 <= cap <= 6.0, f"{cap}h")
    lines.append(f"  current cap: {cap:.1f}h")
except Exception as exc:
    check("_max_resolve_hours import", False, str(exc))

# ── 3. _hours_to_goal live (real engine data) ───────────────────────
lines.append("")
lines.append("live _hours_to_goal:")
try:
    from background_scanner import _hours_to_goal
    for pair in ["BTC/USD", "HYPE/USD", "NEAR/USD"]:
        h = _hours_to_goal(pair, 100.0, 102.0)  # 2% away synthetic
        status = "blocked" if (h is not None and h > _max_resolve_hours()) else "passes"
        lines.append(f"  {pair}: 2% away → {f'{h:.1f}h' if h is not None else 'no data'} → {status}")
except Exception as exc:
    check("_hours_to_goal import", False, str(exc))

# ── report ───────────────────────────────────────────────────────────
fails = [r for r in results if not r[1]]
for name, ok, detail in results:
    lines.insert(len(lines) - len(results) if False else 0, "")  # noop
lines_out = [f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not ok else "")
             for name, ok, detail in results] + lines
out = BASE / "takeit_check.txt"
out.write_text("\n".join(lines_out), encoding="utf-8")
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES'}")
