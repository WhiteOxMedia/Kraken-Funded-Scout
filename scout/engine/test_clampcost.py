"""test_clampcost.py — pins the clamp-cost study + its monthly driver.

The study (diag_clampcost.py) prices the personal-bar clamp bug by
replaying graded tide-out burials through BOTH clamp worlds. These pins
run it HERMETICALLY (tmpdir paths, synthetic outcome rows, the store +
live-cut replays stubbed to a known adjustment) so the battery never
touches the production ledgers or the deep store:

  1. THE VINDICATED ARC — a payer in the erased band: learned bar 86,
     adj +3.0, quick 83 -> bugged world buries (bar clamped back to 86),
     fixed world releases (bar 83). $791.25 x +2.5% = +$19.78.
  2. THE COSTING ARC — the same release with a -3.0% outcome: the fixed
     world ships a LOSER the clamp blocked: cost = 19.78 - (-23.74)
     shape -> -$23.74. The study must price denials that SAVED money
     negative — it is a two-sided ledger.
  3. THE LEDGER — run_study appends one JSONL record per pass with the
     summary fields (window, counts, cost, position).
  4. THE DRIVER — monthly_driver_loop ticks on a fast clock, appends
     per pass, and dies cleanly on stop.set().
  5. THE EMPTY WINDOW — no outcomes rows: zeros, never an exception.

Run: .venv\\Scripts\\python.exe -X utf8 test_clampcost.py
Writes _clampcostpins_check.txt; exit 1 on any FAIL.
"""
import json
import sys
import threading
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

import diag_clampcost as dc
import scratch_space   # scratch policy: nothing volatile lands in the repo root

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


def _hermetic(tmp: Path, events: list[dict]):
    """Point the study at tmpdir files with the given synthetic rows and
    stub the replay to a known +3.0 store adjustment, no live cut."""
    dc.OUTCOMES = tmp / "snapshot_outcomes.jsonl"
    dc.LEDGER = tmp / "clampcost_monthly.jsonl"
    dc.OUT = tmp / "clampcost_check.txt"
    dc._store_adj_at = lambda ts, pair, state: 3.0   # PUMP's earned edge
    dc._live_pts_at = lambda ts, pair, state: 0.0
    dc._position_usd = lambda: 1055.0
    with open(dc.OUTCOMES, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _row(pair: str, end_pct: float) -> dict:
    return {
        "ts": "2026-09-16T10:00:00-06:00",
        "pair": pair, "gate": "tide-out", "quick": 83.0, "brain": None,
        "floor": 89.0, "tide": "OUT 14%", "rot": "08-12",
        "detail": ("the OUT tide's price on the bot's ledger: quick 83.0 "
                   "vs personal bar 86 (learned OUT bar 86 (payers 10.9% "
                   "over 479h of deep history))"),
        "outcome": {"end_move_pct": end_pct, "mfe_pct": end_pct + 1.0,
                    "max_dd_pct": -1.0, "payoff": ("winner" if end_pct > 0
                                                   else "dud"),
                    "speed": "1h", "min_to_goal_min": 42},
    }


def main():
    tmp = Path(tempfile.mkdtemp(prefix="clampcost_pins_"))

    # 1. the vindicated arc: the erased band's payer
    _hermetic(tmp, [_row("PUMP/USD", 2.5)])
    s = dc.run_study()
    check("vindicated arc: bugged world buries the band payer (0 released)",
          s["bugged_released"] == 0, s)
    check("vindicated arc: fixed world releases it (1 released)",
          s["fixed_released"] == 1, s)
    # $1055 x 0.75 (conf 83 -> the 75-rung) x +2.5% = $19.78
    check("vindicated arc: cost +19.78 USD (hand arithmetic)",
          abs(s["cost_usd"] - 19.78) < 0.011, s["cost_usd"])
    check("vindicated arc: the txt names the clamp as the eraser",
          "THE CLAMP BUG COST: +19.78" in Path(s and dc.OUT).read_text(encoding="utf-8")
          or "+19.78" in dc.OUT.read_text(encoding="utf-8"))

    # 2. the costing arc: the same release ships a loser
    _hermetic(tmp, [_row("PUMP/USD", -3.0)])
    s2 = dc.run_study()
    # $791.25 x -3.0% = -$23.74 — the fixed world LOST this; the cost of
    # the clamp is NEGATIVE here (the denial saved money)
    check("costing arc: the fix's release priced -23.74 USD",
          abs(s2["cost_usd"] - (-23.74)) < 0.011, s2["cost_usd"])
    check("costing arc: the release set is still 1/0 (same gate math)",
          s2["fixed_released"] == 1 and s2["bugged_released"] == 0, s2)

    # 3. the ledger: one record per pass, the summary fields present
    led = [json.loads(l) for l in dc.LEDGER.read_text(encoding="utf-8").splitlines()
           if l.strip()]
    check("ledger: two passes appended two records", len(led) == 2, len(led))
    check("ledger: the record carries the study's fields",
          all(k in led[0] for k in ("ts", "graded_tideout", "priced",
                                    "bugged_released", "fixed_released",
                                    "cost_usd", "cost_mfe_usd",
                                    "position_usd", "pass", "fail")),
          sorted(led[0].keys()))
    check("ledger: the cost records round-trip the arcs",
          abs(led[0]["cost_usd"] - 19.78) < 0.011
          and abs(led[1]["cost_usd"] - (-23.74)) < 0.011,
          [r["cost_usd"] for r in led])

    # 4. the driver: fast-clock ticks, clean stop
    n_before = len(led)
    stop = threading.Event()
    th = threading.Thread(target=dc.monthly_driver_loop,
                          args=(stop, 0.05, 0.2), daemon=True)
    th.start()
    time.sleep(1.2)
    stop.set()
    th.join(timeout=5)
    led2 = [json.loads(l) for l in dc.LEDGER.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    check("driver: appended per tick and stopped cleanly",
          not th.is_alive() and len(led2) > n_before,
          f"{len(led2)} records (was {n_before})")

    # 5. the empty window: zeros, never an exception
    _hermetic(tmp, [])
    s3 = dc.run_study()
    check("empty window: zeros without exception",
          s3["graded_tideout"] == 0 and s3["cost_usd"] == 0.0
          and s3["pass"] >= 1 and s3["fail"] == 0, s3)

    lines.append(f"RESULT: {PASS} passed, {FAIL} failed")
    (scratch_space.report_temp('_clampcostpins_check.txt')).write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
