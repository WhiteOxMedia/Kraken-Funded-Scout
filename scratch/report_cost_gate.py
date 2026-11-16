"""report_cost_gate.py — what the measured table does to sizing, pair by pair.

Answers the question the guard exists for: which pairs would be REFUSED by the cost
gate (measured round trip at/above the edge), and which would be sized under $1 by the
death-line rule at the live balance. A pair that shows up as "death cap" while its real
problem is a 2% spread is the silent-zero failure this report is meant to surface.

    python scratch/report_cost_gate.py
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import contest_account as ca
import spread_table as st

BAL = float(ca.load_state()["current_balance"])
START = float(ca.load_state()["start_balance"])
STOP = 2.0
SIZE_FRAC = 0.09          # contest_loop's own fraction
BUFFER_FLOOR = START * 0.025


def spend_for(pair: str, bal: float = BAL) -> float:
    return ca.hard_death_entry_usd(
        bal, START, STOP, SIZE_FRAC, pair=pair,
        window_dd_pct=max(STOP, ca.DEFAULT_WINDOW_DD_PCT),
        min_buffer_usd=BUFFER_FLOOR, halted=False)


def main():
    table = st.load_table()
    rows = []
    for pair, r in (table.get("pairs") or {}).items():
        g = st.cost_gate(pair, table=table)
        rows.append((pair, g, r, spend_for(pair)))
    rows.sort(key=lambda x: -x[1]["rt_pct"])

    print(f"balance ${BAL:,.2f}  start ${START:,.2f}  stop {STOP:g}%  "
          f"frac {SIZE_FRAC:g}  edge {st.GROSS_EDGE_PCT}%  "
          f"ceiling {st.MAX_RT_PCT:g}%  buffer floor ${BUFFER_FLOOR:,.2f}\n")
    print(f"{'pair':<10} {'rt%':>9} {'days':>4} {'day_n':>5} {'net edge%':>10} "
          f"{'verdict':>8} {'spend $':>9}")
    refuse, sub1, thin, ok = [], [], [], []
    for pair, g, r, spend in rows:
        print(f"{pair:<10} {g['rt_pct']:>9.4f} {g.get('days', 0):>4} "
              f"{g.get('day_n', 0):>5} {g['net_edge_pct']:>10.4f} "
              f"{g['verdict']:>8} {spend:>9.2f}")
        {"refuse": refuse, "thin": thin, "ok": ok}.get(g["verdict"], []).append(pair)
        if spend < 1.0:
            sub1.append((pair, spend, g["verdict"]))

    print(f"\nrefused by the cost gate ({len(refuse)}): {', '.join(refuse) or 'none'}")
    print(f"thin (trades, but <=25% of the edge survives) ({len(thin)}): "
          f"{', '.join(thin) or 'none'}")
    print(f"clean ({len(ok)}): {len(ok)} pairs")
    print(f"sized under $1 ({len(sub1)}): "
          + (", ".join(f"{p} ${s:.2f}" for p, s, _ in sub1) if sub1 else "none"))
    json.dump({"balance": BAL, "refuse": refuse, "thin": thin,
               "under_1_usd": sub1,
               "pairs": {p: {"rt": g["rt_pct"], "verdict": g["verdict"],
                             "spend": round(s, 4)} for p, g, _r, s in rows}},
              open(os.path.join(HERE, "_cost_gate_report.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
