"""THE CANARY ENABLEMENT ONE-LINER (the Sep-28 late checklist's final
step): read the focused sensitivity extract, and arm the canary cohort
ONLY if the worst single-book loss sits inside the standing cap — the
'if replay looks safe' gate, executable instead of aspirational.

    uv run python -X utf8 ops/canary_enable.py --symbols BTC,ETH,SOL --yes
    uv run python -X utf8 ops/canary_enable.py --off        # the stop
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

    if not args.yes:
        print(f"dry run — would arm with the most conservative safe "
              f"cell: alpha={pick['alpha']}, delta_max={pick['delta_max']}, "
              f"daily_cap={pick['daily_cap']}. Re-run with --yes to arm "
              "(time-boxed, auto-rollback armed).")
        return 0

    flipped = cr.flip_canary(
        f"canary-{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    print("ARMED:", json.dumps({**flipped,
                                "alpha": pick["alpha"],
                                "delta_max": pick["delta_max"],
                                "daily_cap": pick["daily_cap"]}))
    print("rollback anytime: uv run python -X utf8 ops/canary_enable.py --off")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
