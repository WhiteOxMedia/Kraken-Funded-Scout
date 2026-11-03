"""_preset_tierscaled.py — THE TIER-SCALED PRESET BACKTEST (Sep 27).

The preset framework re-run at the scout's ACTUAL move sizes, answering
the standing critique: the swing-scale presets (+6..15% guards, +8..12%
trails) never intersect a scalper book whose goals land near +1-2%.
Tier scaling maps the vocabulary onto this tier:

  guard: AT THE TRADE'S OWN GOAL (entry * (1 + goal/100)) — the guard
         can never cap a goal, so the fixed-guard poison that killed
         the swing presets is gone BY CONSTRUCTION. What remains is
         the pure trail-width question the presets were always asking.
  trail: 1.5% / 2.0% / 3.0% off the running peak — the incumbent
         ratchet's own width family (CONTEST_TRAIL_PCT -> 1.5).
  PRESET SEMANTICS KEPT: the trail runs FROM ENTRY (no arm bar) — this
  is what distinguishes a preset from the hybrid studies (which arm at
  90% of goal). Losers get cut early too; that is the preset deal.

Walk: 1h candles, guard-first bar ordering, untriggered trades keep
their ACTUAL exit, fees via net(), horizons 4h/24h/48h with per-table
CENSORSHIP counts (a trade whose window runs past data end keeps its
actual exit; untriggered is NOT proof of no-fill — fills beyond data
end stay unknown). Baseline: the ladder's ACTUAL net. Control:
took-the-goal cohort must not be made worse. Read-only.
"""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _verdict_study as vs  # noqa: E402

PRESETS = [
    ("conservative: goal-guard + 1.5% trail", 1.5),
    ("balanced:     goal-guard + 2.0% trail", 2.0),
    ("aggressive:   goal-guard + 3.0% trail", 3.0),
]
HORIZONS = [("4h", 4 * 3600000), ("24h", 24 * 3600000), ("48h", 48 * 3600000)]
_MAX_TS = vs._db().execute("SELECT MAX(ts) FROM ohlcv").fetchone()[0]


def policy_exit(g, trail_pct, horizon_ms):
    """(exit_px, 'guard'|'trail') or None if untriggered in-window.
    The guard level is THIS trade's own goal (tier-scaled)."""
    entry = g["entry"]
    guard = entry * (1 + g["goal"] / 100.0)
    peak, stop = entry, None
    for ts, hi, lo in vs._db().execute(
            "SELECT ts, high, low FROM ohlcv WHERE pair=? AND ts>=? "
            "AND ts<=? ORDER BY ts",
            (g["pair"], int(g["en_ms"]), int(g["ex_ms"] + horizon_ms))):
        if hi >= guard:                          # GUARD FIRST: bank at goal
            return guard, "guard"
        if hi > peak:
            peak = hi
            stop = peak * (1 - trail_pct / 100.0)
        if stop is not None and lo <= stop:
            return stop, "trail"
    return None


graded = vs.grade_trades()
groups = defaultdict(list)
for g in graded:
    groups[str(g.get("verdict")).split(" - ")[0]].append(g)
act_ctrl = sum(vs.net(g["entry"], g["exit"], g["size"])
               for g in graded if "took the goal" in str(g.get("verdict")))

for label, H in HORIZONS:
    censored = sum(1 for g in graded if float(g["ex_ms"]) + H > _MAX_TS)
    act = sum(vs.net(g["entry"], g["exit"], g["size"]) for g in graded)
    print(f"\n{'=' * 26} TIER-SCALED, HORIZON {label} {'=' * 26}")
    print(f"trades: {len(graded)}   CENSORED (window past data end): "
          f"{censored}/{len(graded)} — untriggered is NOT proof of "
          f"no-fill; the tail beyond data end stays unknown")
    print(f"{'preset':<40} | {'realized $':>10} | {'vs actual':>9} | "
          f"{'banks':>5} | {'trail x':>7} | {'ladder x':>8} | ctrl")
    print("-" * 108)
    rows = {}
    for name, w in PRESETS:
        tot, ng, nt = 0.0, 0, 0
        for g in graded:
            ex = policy_exit(g, w, H)
            if ex is None:
                tot += vs.net(g["entry"], g["exit"], g["size"])
            else:
                px, which = ex
                tot += vs.net(g["entry"], px, g["size"])
                ng += which == "guard"
                nt += which == "trail"
        dc = 0.0
        for g in groups.get("took the goal", []):
            ex = policy_exit(g, w, H)
            dc += vs.net(g["entry"], (ex or (g["exit"],))[0], g["size"])
        ok = "OK" if dc >= act_ctrl - 1e-9 else f"BROKEN ({dc - act_ctrl:+.2f})"
        rows[name] = tot
        print(f"{name:<40} | {tot:>10.2f} | {tot - act:>+9.2f} | "
              f"{ng:>5d} | {nt:>7d} | {len(graded) - ng - nt:>8d} | {ok}")
    print(f"{'ACTUAL (the ladder)':<40} | {act:>10.2f} | "
          f"{'—':>9} | {'—':>5} | {'—':>7} | {len(graded):>8d} | ctrl {act_ctrl:.2f}")

    print(f"-- per-class delta $ vs actual ({label}) --")
    print("class".ljust(18) + "".join(f"{'w' + str(w):>16}" for _, w in PRESETS))
    for cn in sorted(groups):
        line = cn.ljust(18)
        for name, w in PRESETS:
            d = 0.0
            for g in groups[cn]:
                ex = policy_exit(g, w, H)
                px = ex[0] if ex else g["exit"]
                d += (vs.net(g["entry"], px, g["size"])
                      - vs.net(g["entry"], g["exit"], g["size"]))
            line += f"{d:>16.2f}"
        print(line)

    winner = max(rows, key=rows.get)
    if rows[winner] > act + 1e-9:
        print(f"VERDICT ({label}): {winner} realizes the most "
              f"({rows[winner]:.2f} vs actual {act:.2f})")
    else:
        print(f"VERDICT ({label}): no tier-scaled preset beats the ladder "
              f"(best {rows[winner]:.2f} vs actual {act:.2f})")
