"""One-off era comparison (scratch convention _*.py): the verdict-cost
study re-run, split at the ratchet re-base (2026-09-21, commit fc00c9c).

The honest framing up front: the re-base has been live HOURS, not a
week. 46 of the 48 closed trades predate it. This script quantifies
exactly what the post-era contains and what the pre-era's stayed-too-
long cost looked like, so the comparison says what the data can prove
and nothing more. Read-only: uses the study's own grade_trades().
"""
from collections import defaultdict

import _verdict_study as vs

CUTOFF = "2026-09-21"   # the re-base day (fc00c9c) — trades sold on/after ran under the new ratchet


def main():
    graded = vs.grade_trades()
    print(f"graded {len(graded)} trades total")

    eras = {"pre": [], "post": []}
    for g in graded:
        eras["post" if g["date"] >= CUTOFF else "pre"].append(g)

    for era, rows in eras.items():
        print(f"\n=== {era.upper()} re-base ({len(rows)} trades, "
              f"{sorted({r['date'] for r in rows}) or ['-']}) ===")
        if not rows:
            print("  (no trades)")
            continue
        by = defaultdict(lambda: {"n": 0, "left": 0.0})
        for g in rows:
            by[g["verdict"]]["n"] += 1
            by[g["verdict"]]["left"] += g["left"]
        for k, v in sorted(by.items(), key=lambda kv: -kv[1]["left"]):
            print(f"  {k:<46}{v['n']:>3}  ${v['left']:>9,.2f}")
        stl = [g for g in rows if "stayed too long" in g["verdict"]]
        if stl:
            print("  stayed-too-long trades:")
            for g in stl:
                peak_pct = (g["peak"] / g["entry"] - 1.0) * 100.0
                print(f"    {g['date']} {g['pair']:<10} goal {g['goal']:.1f}%  "
                      f"peak +{peak_pct:.1f}%  realized ${g['realized']:.2f}  "
                      f"left ${g['left']:.2f}")
        else:
            print("  stayed-too-long: none in this era")
        # what the ratchet would have done in this era (live policy, leak set)
        leak = [g for g in rows if "stayed too long" in g["verdict"]
                or "exited too soon" in g["verdict"]]
        d, n = vs._sim(leak, 1.5)
        print(f"  live-era check — hypothetical 1.5% trail on this era's leak set: "
              f"n={n}, delta ${d:,.2f}")

    print("\n=== WHAT THE DATA CAN PROVE ===")
    pre, post = eras["pre"], eras["post"]
    print(f"pre-era: {len(pre)} trades over "
          f"{len({r['date'] for r in pre})} days — the stayed-too-long "
          f"baseline the re-base was built from.")
    print(f"post-era: {len(post)} trades on {len({r['date'] for r in post})} day(s) — "
          "NOT a week. Any 'comparison' of stayed-too-long cost between "
          "eras today would be comparing 46 trades against a sample that "
          "cannot support it.")
    print("What IS meaningful now: the re-based ratchet's engagement rate "
          "(see the study's RATCHET CHECK — the retired +$100/+$200 dial "
          "engaged on 0/48; the +1%/+2% peak-percent dial engages on any "
          "real run), and the trail-sim rows the study reprints each run.")


if __name__ == "__main__":
    main()
