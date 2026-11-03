"""diag_ftdip.py — THE FOLLOW-THROUGH DIP PROBE (Sep 19).

QUESTION (user, Sep 19): the brain's two newest band-35 buys — the hours
that dropped the follow-through arming rate below its 60% bar — did they
pay or bleed? And is the 0.69 -> 0.50 dip noise or regime?

METHOD: replay direction_notes.jsonl through the grade's own machinery
(_grade_one: the brain re-read at each captured hour, the 4h forward
window priced in the popup convention), take every band-35 buy row in
capture order, and report each one's outcome. Then price the dip
directly: binomial tails on the rate series, and the counterfactual
dollars of the rows that disarmed the gate (the 5 brain-holds that
landed at 23:57) — their grade also answers whether holding was right.

Run: .venv\\Scripts\\python.exe -X utf8 diag_ftdip.py
Writes _ftdip.txt. Read-only; the brain replay is CPU-only.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

OUT = Path(__file__).parent / "_ftdip.txt"
FT_BAND = "35"
POSITION_USD = 1027.40  # today's live contest size (direction-grade convention)
POPUP_GOAL = 2.0        # the popup convention: a $ win = +2% MFE in 4h

# noise-or-regime: binomial tail P(X >= k | n, p0). 13/26 vs the 9/13 era:
# if the TRUE rate were still the armed-era 0.69, seeing only 13 buys in 26
# rows has this probability. Two-sided-ish reporting keeps it honest.


def binom_tail(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))


def binom_lower(k: int, n: int, p: float) -> float:
    """THE DIP'S OWN TEST: P(X <= k | n, p) — seeing k-or-fewer buys when the
    true rate were still p. Small = the drop is not sampling noise."""
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(0, k + 1))


def main() -> int:
    import diag_directiongrade as dg

    rows: list[dict] = []
    if dg.NOTES.exists():
        for line in dg.NOTES.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                if isinstance(d, dict) and d.get("pair"):
                    rows.append(d)
            except Exception:
                continue

    # dedupe on (pair, hour bucket), same as run_grade
    seen: set[tuple] = set()
    notes = []
    for d in rows:
        try:
            ts = time.mktime(time.strptime(str(d["ts"])[:19], "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            continue
        key = (str(d["pair"]), int(ts // 3600))
        if key in seen:
            continue
        seen.add(key)
        d = dict(d)
        d["_ts"] = float(ts)
        notes.append(d)
    notes.sort(key=lambda x: x["_ts"])

    graded = []
    # run_grade's pair-format bridge: index both stem and venue forms
    by_pair = {}
    for p in sorted(__import__("glob").glob(str(dg.BASE / dg.STORES_GLOB))):
        stem = Path(p).name.replace("-1h.feather", "")
        by_pair[stem] = Path(p)
        by_pair[stem.replace("_", "/")] = Path(p)
    for d in notes:
        store = by_pair.get(d["pair"])
        if store is None:
            continue
        g = dg._grade_one(store, d["_ts"])
        if g is None:
            continue
        g["pair"] = d["pair"]
        g["quick_action"] = str(d.get("quick_action") or "hold")
        g["why"] = str(d.get("why") or "")[:60]
        g["ts"] = d["_ts"]
        graded.append(g)

    b35 = [g for g in graded if dg._band_of(float(g["brain_conf"])) == FT_BAND]
    buys = [g for g in b35 if g["brain_action"] == "buy"]
    holds = [g for g in b35 if g["brain_action"] != "buy"]

    lines = ["THE FOLLOW-THROUGH DIP PROBE — the two newest band-35 buys, and",
             "whether the 0.69 -> 0.50 arming-rate dip is noise or regime.",
             f"graded rows: {len(graded)} total, band-35: {len(b35)} "
             f"({len(buys)} buys / {len(holds)} holds)", "",
             "ALL BAND-35 BUYS IN CAPTURE ORDER (oldest first):",
             f"{'captured':<17}{'pair':<13}{'brain':>5}  {'end4h%':>7}  "
             f"{'$@' + str(int(POSITION_USD)):>8}  {'ran2%':<6}{'#'}"]
    for i, g in enumerate(buys):
        usd = POSITION_USD * g["end"] / 100.0
        lines.append(f"{time.strftime('%m-%d %H:%M', time.gmtime(g['ts'])):<17}"
                     f"{g['pair']:<13}{g['brain_conf']:>5.1f}  {g['end']:>7.2f}  "
                     f"{usd:>+8.2f}  {'RAN' if g['ran'] else '-':<6}{i + 1}")

    n = len(buys)
    ran = sum(1 for g in buys if g["ran"])
    tot_usd = sum(POSITION_USD * g["end"] / 100.0 for g in buys)
    lines += ["",
              f"THE TWO NEWEST BUYS (the arming-rate droppers):"]
    for g in buys[-2:]:
        usd = POSITION_USD * g["end"] / 100.0
        lines.append(f"  {g['pair']} @ {time.strftime('%m-%d %H:%M', time.gmtime(g['ts']))} "
                     f"(brain {g['brain_conf']:.0f}%): end {g['end']:+.2f}% = "
                     f"{usd:+.2f} dollars, {'RAN +2% MFE' if g['ran'] else 'never ran'} — "
                     f"{('PAID' if g['end'] > 0 else 'BLED') if g['end'] != 0 else 'flat'}")

    # noise or regime: split the series in time halves + the binomial test
    half = n // 2
    early = buys[:half]
    late = buys[half:]
    e_ran = sum(1 for g in early if g["ran"])
    l_ran = sum(1 for g in late if g["ran"])
    # the dip's own statistic, BOTH samples: this replay (12/24) and the
    # production record (13/26 — two rows sit at a store edge here)
    p_obs = n / len(b35) if b35 else 0.0
    tail_replay = binom_lower(n, len(b35), 0.69) if b35 else None
    tail_record = binom_lower(13, 26, 0.69)
    held_ran = sum(1 for g in holds if g["ran"])
    held_usd = sum(POSITION_USD * g["end"] / 100.0 for g in holds)

    lines += ["",
              f"BUY-RATE MATH: this replay {n}/{len(b35)} = {p_obs:.2f}; the "
              f"production record 13/26 = 0.50; the gate needs 0.60",
              f"  LOWER-TAIL TEST (if the TRUE rate were still 0.69):",
              f"  seeing <= {n} buys in {len(b35)} rows: p = {tail_replay:.3f}"
              if tail_replay is not None else "  (no band-35 rows)",
              f"  seeing <= 13 buys in 26 rows (the record): p = {tail_record:.3f}",
              f"  CAVEAT: 0.69 itself came from 13 rows — its own 95% CI "
              f"(~0.42-0.88) contains 0.50, so the test overstates certainty",
              f"  early {len(early)} buys: {e_ran} ran | late {len(late)} buys: "
              f"{l_ran} ran (ran-rate {e_ran / max(len(early), 1):.2f} -> "
              f"{l_ran / max(len(late), 1):.2f})",
              f"THE {len(holds)} HOLDS (the rows that dropped the rate): their 4h "
              f"outcome was ${held_usd:+.2f} total, {held_ran} ran — the veto's "
              f"own grade, and the SEPARATION test: buys must beat holds for the "
              f"gate's premise to hold",
              f"ALL BAND-35 ROWS: {tot_usd:+.2f} dollars from the buys "
              f"({tot_usd / max(n, 1):+.2f}/buy), {ran}/{n} ran the +2% bar"]

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
