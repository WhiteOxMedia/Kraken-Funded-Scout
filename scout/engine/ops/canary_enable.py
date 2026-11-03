"""THE CANARY ENABLEMENT ONE-LINER (the Sep-28 late checklist's final
step): read the focused sensitivity extract, and arm the canary cohort
ONLY if the worst single-book loss sits inside the standing cap — the
'if replay looks safe' gate, executable instead of aspirational.

    uv run python -X utf8 ops/canary_enable.py --symbols BTC,ETH,SOL --yes
    uv run python -X utf8 ops/canary_enable.py --off        # the stop

THE CERTIFIED BAND (Sep 30, the guard-factor validation's last wire):
the arm rehearsal (run_canary arm=True) rehearses the arm THROUGH the
evaluate() thresholds, and evaluate() refuses to certify the net
threshold from a self-derived CI — a missing prior band reads
band_certified False and the verdict INCONCLUSIVE (the stale-
certification hole, closed in task 55). So the gate derives the band
from the sweep evidence it already holds: the CHOSEN CELL's own
bootstrap band of applied deltas (ci_p5/ci_p95) — the distribution the
replay actually produced — scaled to the rehearsal's cycle count by
evaluate() itself (the band is per-cycle; the threshold multiplies by
n_applied). The rehearsal then grades the arm against evidence, not
against nothing: production arms a CERTIFIED band or the arm is not
performed at all.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORST_LOSS_LIMIT = float(os.environ.get("CANARY_WORST_LOSS_LIMIT", "0.10"))
# NOTE (the round-2 review): 0.10 is the standing DELTA_MAX, and the
# dmax=0.10 cell's worst loss ECHOES its own cap — so the default limit
# can only ever qualify the dmax=0.05 cell (strict `<`). That is the
# intended posture: at the default, only the conservative cell may arm.
# The refusal branch is REACHABLE whenever the evidence says so: tighten
# the env (0.03 refuses every focused cell — pinned in the review run)
# or let a future focused extract widen beyond the cap.
# the standing DELTA_MAX — a focused cell whose worst single-book loss
# EXCEEDS it is not safe (a cell AT the limit is its own cap echoed
# back — not evidence of safety, so the pick below demands strictly
# inside). CANARY_WORST_LOSS_LIMIT makes the gate TUNEABLE — the
# operator can tighten (0.05) or loosen; the refusal branch is
# reachable whenever the evidence says so (see the NOTE above — at the
# default only the dmax=0.05 cell can qualify, by construction).

# THE ARM REHEARSAL (Sep 30): the staging exercise's shape — cycles and
# the seeded rotation the cohort walks. The rehearsal is the last gate
# before the flip: the machine grades its own armed run, and a breach
# executes the REAL rollback before production ever rides the cell.
REHEARSAL_CYCLES = 30
REHEARSAL_SEED = {"B": 12, "r": 0.45}


def prior_band_for_cell(cell: dict) -> tuple[float, float]:
    """The sweep-derived CERTIFICATION BAND for the chosen cell: the
    cell's own bootstrap band (p5, p95) of the deltas its replay
    applied — the same evidence the worst-loss gate read, now carried
    through the arm as the external band evaluate() demands. A cell
    with no applied deltas certifies nothing (0, 0 is the honest empty
    band: an idle rehearsal stays inside it, a net-moving one does
    not)."""
    return (float(cell.get("ci_p5", 0.0)),
            float(cell.get("ci_p95", 0.0)))


def main() -> int:
    import auto_adjust as aa
    import canary_runner as cr
    import simulate_sweep as ss

    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTC,ETH,SOL")
    ap.add_argument("--yes", action="store_true",
                    help="required to actually flip the dial")
    ap.add_argument("--off", action="store_true",
                    help="roll the canary back instead")
    args = ap.parse_args()

    if args.off:
        print(json.dumps(cr.rollback()))
        return 0

    history = ss.load_history(30)
    cells = ss.focused_sweep(history)
    # STRICTLY inside the limit: a cell whose worst loss EQUALS the cap
    # is just its own dmax echoed back (dmax=0.10 always shows -0.10) —
    # that is not evidence of safety (the review's major). The pick is
    # the most conservative safe cell: smallest alpha, then smallest
    # dmax — arming parameters come from the EVIDENCE, not the defaults.
    safe = [c for c in cells
            if abs(c["worst_single_book_loss"]) < WORST_LOSS_LIMIT]
    print("focused extract:")
    for c in cells:
        mark = ("SAFE" if abs(c["worst_single_book_loss"])
                < WORST_LOSS_LIMIT else "AT/OVER CAP")
        print(f"  alpha={c['alpha']} dmax={c['delta_max']}: "
              f"worst single-book {c['worst_single_book_loss']:+.4f} "
              f"[{mark}], {c['applied_count']} applies")
    if not safe:
        print("REFUSING to arm: no focused cell sits strictly inside "
              f"the {WORST_LOSS_LIMIT} worst-loss cap — run the full "
              "sweep and re-read the governance policy.")
        return 1
    pick = min(safe, key=lambda c: (c["alpha"], c["delta_max"]))
    band = prior_band_for_cell(pick)
    params = {"alpha": pick["alpha"], "delta_max": pick["delta_max"],
              "daily_cap": pick["daily_cap"]}

    if not args.yes:
        print(f"dry run — would arm with the most conservative safe "
              f"cell: alpha={pick['alpha']}, delta_max={pick['delta_max']}, "
              f"daily_cap={pick['daily_cap']}, certified band "
              f"[{band[0]:+.4f}, {band[1]:+.4f}] per cycle (the chosen "
              f"cell's own replayed-delta band). Re-run with --yes to "
              f"rehearse the arm and flip (time-boxed, auto-rollback "
              f"armed).")
        return 0

    # THE ARM REHEARSAL — the gate grades its own arm before the flip:
    # run_canary arm=True flips the cohort WITH the selected cell,
    # walks the machine's rule over the rehearsal cycles under the
    # sweep-derived band, and a breach executes the REAL rollback —
    # production never rides a cell the rehearsal failed. The LIVE
    # cohort guard stays armed: a rehearsal that resolves to the live
    # cohort file is refused (run_canary's own door) — set
    # AUTO_ADJUST_CANARY_FILE to a staging path for a deliberate
    # live re-arm with arm_confirm=True.
    rehearsal = cr.run_canary(
        [s.strip().split("/")[0].upper()
         for s in args.symbols.split(",") if s.strip()],
        REHEARSAL_SEED, prior_band=band, cycles=REHEARSAL_CYCLES,
        arm=True, params=params)
    print(f"arm rehearsal: {rehearsal['status']} "
          f"(band certified: "
          f"{rehearsal['evaluation'].get('band_certified')}, worst "
          f"{rehearsal['evaluation'].get('worst'):+.4f} vs cell cap "
          f"{rehearsal['evaluation'].get('worst_cap'):+.4f}, report "
          f"{rehearsal.get('report')})")
    if rehearsal["status"] not in ("pass_continue", "rolled_back"):
        # breach_simulated can only mean the rehearsal ran without a
        # flip (a future caller shape) — refuse loudly either way: no
        # confirmed arm sits behind an ungraded rehearsal.
        print("REFUSING to report the arm: the rehearsal did not "
              f"pass (status {rehearsal['status']}, reasons "
              f"{rehearsal['evaluation'].get('reasons')}) — the "
              "rollback, if any, already executed.")
        return 1

    print("ARMED:", json.dumps({**(rehearsal.get("dial") or {}),
                                "alpha": pick["alpha"],
                                "delta_max": pick["delta_max"],
                                "daily_cap": pick["daily_cap"],
                                "certified_band": list(band),
                                "rehearsal": rehearsal["status"],
                                "run_id": rehearsal["run_id"]}))
    print("rollback anytime: uv run python -X utf8 ops/canary_enable.py --off")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
