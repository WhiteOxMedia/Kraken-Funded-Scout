"""_preset_backtest.py — THE PRESET BACKTEST, HORIZON-EXTENDED (Sep 27).

The conservative / balanced / aggressive pairing presets as COMPLETE
exit policies (guard limit + trailing stop) over the graded ledger,
now at THREE post-exit horizons: 4h (the study's original window),
24h, and 48h — so the aggressive preset (no guard, 12% trail) is
actually exercised instead of firing zero times.

CENSORSHIP HONESTY: the OHLCV table ENDS at a fixed timestamp. A trade
whose exit + horizon runs past data end is CENSORED: its untriggered
status means the preset did not fire *within available data*, not that
it never would — fills beyond the data window stay unknown, and those
trades keep their ACTUAL exit in the tally (the fallback rule). Every
horizon table reports its censored count; read wide-horizon deltas as
lower bounds on preset behavior, strongest for the most recent trades.

Sim rules per trade (1h candles, fees via net(), entry = real entry):
  profit guard: limit at entry*(1+g/100), fills when HIGH reaches it;
  trailing stop: width w% off the running peak, triggers when LOW
                 touches peak*(1-w/100);
  same-bar rule: GUARD FIRST (conservative ordering; 1h bars make the
                 intrabar order ambiguous — documented assumption);
  untriggered:   the trade keeps its ACTUAL exit.
Baseline row: the ladder's ACTUAL realized net. Control: took-the-goal
cohort must not be made worse (guard discipline). Read-only.
"""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _verdict_study as vs  # noqa: E402

PRESETS = {
    "conservative": {"guard": 6.0, "trail": 5.0},
    "balanced":     {"guard": 15.0, "trail": 8.0},
    "aggressive":   {"guard": None, "trail": 12.0},
}
HORIZONS = [("4h", 4 * 3600000), ("24h", 24 * 3600000), ("48h", 48 * 3600000)]

_MAX_TS = vs._db().execute("SELECT MAX(ts) FROM ohlcv").fetchone()[0]


def policy_exit(g, guard_pct, trail_pct, horizon_ms):
    """(exit_px, 'guard'|'trail') or None if untriggered in-window."""
    entry = g["entry"]
    guard = entry * (1 + guard_pct / 100.0) if guard_pct else None
    peak, stop = entry, None
    end = int(g["ex_ms"] + horizon_ms)
    for ts, hi, lo in vs._db().execute(
            "SELECT ts, high, low FROM ohlcv WHERE pair=? AND ts>=? "
            "AND ts<=? ORDER BY ts",
            (g["pair"], int(g["en_ms"]), end)):
        if guard is not None and hi >= guard:      # GUARD FIRST
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
    print(f"\n{'=' * 30} HORIZON {label} {'=' * 30}")
    print(f"trades: {len(graded)}   fees: net() RT_COST   "
          f"CENSORED (horizon past data end): {censored}/{len(graded)} "
          f"— their untriggered status is NOT proof of no-fill; the "
          f"tail beyond data end stays unknown")
    print(f"{'preset':<13} | {'realized $':>10} | {'vs actual':>9} | "
          f"{'guard x':>7} | {'trail x':>7} | {'ladder x':>8} | ctrl guard")
    print("-" * 84)
    rows = {}
    for name, p in PRESETS.items():
        tot, ng, nt = 0.0, 0, 0
        for g in graded:
            ex = policy_exit(g, p["guard"], p["trail"], H)
            if ex is None:
                tot += vs.net(g["entry"], g["exit"], g["size"])
            else:
                px, which = ex
                tot += vs.net(g["entry"], px, g["size"])
                ng += which == "guard"
                nt += which == "trail"
        dc = 0.0
        for g in groups.get("took the goal", []):
            ex = policy_exit(g, p["guard"], p["trail"], H)
            dc += vs.net(g["entry"], (ex or (g["exit"],))[0], g["size"])
        guard_ok = "OK" if dc >= act_ctrl - 1e-9 else f"BROKEN ({dc - act_ctrl:+.2f})"
        rows[name] = tot
        print(f"{name:<13} | {tot:>10.2f} | {tot - act:>+9.2f} | "
              f"{ng:>7d} | {nt:>7d} | {len(graded) - ng - nt:>8d} | {guard_ok}")
    print(f"{'ACTUAL':<13} | {act:>10.2f} | {'—':>9} | "
          f"{'—':>7} | {'—':>7} | {len(graded):>8d} | ctrl {act_ctrl:.2f}")

    print(f"-- per-class delta $ vs actual ({label}) --")
    print("class".ljust(18) + "".join(f"{n:>16}" for n in PRESETS))
    for cn in sorted(groups):
        line = cn.ljust(18)
        for name, p in PRESETS.items():
            d = 0.0
            for g in groups[cn]:
                ex = policy_exit(g, p["guard"], p["trail"], H)
                px = ex[0] if ex else g["exit"]
                d += (vs.net(g["entry"], px, g["size"])
                      - vs.net(g["entry"], g["exit"], g["size"]))
            line += f"{d:>16.2f}"
        print(line)

    winner = max(rows, key=rows.get)
    if rows[winner] > act + 1e-9:
        print(f"VERDICT ({label}): {winner} realizes the most "
              f"({rows[winner]:.2f} vs actual {act:.2f}) — beats the ladder")
    else:
        print(f"VERDICT ({label}): no preset beats the ladder "
              f"(best {winner} {rows[winner]:.2f} vs actual {act:.2f})")
