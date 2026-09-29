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
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORST_LOSS_LIMIT = 0.10   # the standing DELTA_MAX — a focused cell whose
                          # worst single-book loss exceeds it is not safe


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
    safe = [c for c in cells
            if abs(c["worst_single_book_loss"]) <= WORST_LOSS_LIMIT]
    print("focused extract:")
    for c in cells:
        mark = "SAFE" if abs(c["worst_single_book_loss"]) \
            <= WORST_LOSS_LIMIT else "OVER CAP"
        print(f"  alpha={c['alpha']} dmax={c['delta_max']}: "
              f"worst single-book {c['worst_single_book_loss']:+.4f} "
              f"[{mark}], {c['applied_count']} applies")
    if not safe:
        print("REFUSING to arm: no focused cell sits inside the "
              f"{WORST_LOSS_LIMIT} worst-loss cap — run the full sweep "
              "and re-read the governance policy.")
        return 1

    if not args.yes:
        print("dry run — re-run with --yes to arm the cohort "
              "(alpha=0.05, delta_max=0.10, daily_cap=0.20 defaults, "
              "time-boxed, auto-rollback armed).")
        return 0

    flipped = cr.flip_canary(
        f"canary-{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    print("ARMED:", json.dumps(flipped))
    print("rollback anytime: uv run python -X utf8 ops/canary_enable.py --off")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
