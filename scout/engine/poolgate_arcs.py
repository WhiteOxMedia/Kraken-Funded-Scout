"""poolgate_arcs.py — THE SHARED ARC FIXTURES (Sep 13).

WHY THIS EXISTS: diag_poolgate's sim arc streams were the study's
private data — the live pool-gate machines (grade_poolgate,
_poolgate_family_sweep, recommend_pool_min) were pinned in the suite
on DIFFERENT, ad-hoc streams. Two fixtures for one machine means a
semantics drift can pass the suite while the study disagrees with the
grade. This module is the ONE set of streams both consume: the diag
prices its study on these exact records, and the suite pins the LIVE
machines against the SAME records and the diag's canon dollars — so
"the study and the grade agree" is a tested fact, not a hope.

THE FORMAT is the live weekly-history record shape (states -> state ->
in_force/other seats with fires/usd/usd_per_fire, other_src tagging
which seat ships — exactly what _evaluate_flips, _rolling_grade, and
the governance walk read in production). The canon values were
hand-derived in diag_poolgate and are re-derived there each run: if
you change a stream here, the diag's hand-arithmetic canon and the
suite pins must change together or not at all.

THE ARCS (the diag's seeded worked examples; sims never enter the
live trail — these stay fixtures):
  arc 1 — the bench PAYS (OUT): three losing weeks flip the state,
          no re-arm; thresholds have nothing to deny (canon None).
  arc 2 — the bench COSTS (RISING): the good re-arm. Its recovery is
          shaved so the re-arm pool reads +0.47 — strictly between
          the dials: +0.25 lets it re-arm (identical walk, 0.0),
          +0.50 denies a PAYING re-arm and MISSES $180.40. The
          counter-case the family net must carry.
  arc 3 — the re-arm COSTS (FLAT): the bad re-arm at pool +0.19.
          Both graduated dials deny it and SAVE $307.50 — the
          cross-check that agrees with the single-arc study.
  arc 4 — FAT POOL / THIN WEEKS (RISING): the re-arm's 4-week
          window reads +0.7875 (the gate stamps it +0.79 — fat; the
          pool dials deny it on size) but its trailing winning
          ww=2 trailing winning weeks (thin — the streak dials deny
          it on duration). The recovery was REAL: both denials
          delay a re-arm whose aftermath pays, so the families
          price their miss — and the two dials deny on DIFFERENT
          evidence, the disagreement made dollars.
  arc 5 — THIN POOL / DURABLE RECOVERY (FLAT): the re-arm's 4-week
          window reads +0.175 (the gate stamps it +0.17 — thin; the
          pool dials deny it) but ww=4 trailing
          winning weeks (the streak dials clear it outright). The
          recovery proves DURABLE (+$0.20-0.30/week after), so the
          pool denial misses big while the streak dial was right —
          the shape where raising the pool bar costs the most.

Pure data + a builder: importing this module touches no production
state, reads no files, and never enters the live trail.
"""
from pathlib import Path

# the arc-3 fire convention: the fit's fires live vs benched (the
# benched weeks swap them — the shipped regime's fires are the ones
# the quantile's counterfactual replaces)
FI, FO = 82, 46


def wk(tag, live, other, src="quantile", state="FLAT",
       fires_i=None, fires_o=None):
    """One live-format week record; fires_i/fires_o override the arc's
    default fire counts (flipped-regime weeks swap them)."""
    fires_i = FI if fires_i is None else fires_i
    fires_o = FO if fires_o is None else fires_o
    return {"week": tag, "generated_ts": f"{tag} pass",
            "states": {state: {
                "n": fires_i,
                "in_force": {"fires": fires_i, "payers": 3, "rate": 0.3,
                             "usd": round(fires_i * live, 2),
                             "usd_per_fire": round(live, 2)},
                "other": {"fires": fires_o, "payers": 3, "rate": 0.3,
                          "usd": round(fires_o * other, 2),
                          "usd_per_fire": round(other, 2)},
                "other_src": src}}}


# ── arc 1 — the bench PAYS (OUT): three losing weeks, no re-arm ────
ARC1 = [wk("2026-W37", 4.00, 4.82, state="OUT"),
        wk("2026-W38", 4.00 - 0.4, 4.82 + 0.3, state="OUT"),
        wk("2026-W39", 4.00 - 0.5, 4.82 + 0.2, state="OUT"),
        wk("2026-W40", 4.00 - 0.6, 4.82 + 0.1, state="OUT")]

# ── arc 2 — the bench COSTS (RISING): the GOOD re-arm. W42-W43's
#    recovery is shaved (fit cf 4.70/4.60 -> 4.40/4.55) so the re-arm
#    pool reads +0.47 — strictly between the dials; W44-W46 trade the
#    aftermath with the fit live and OUT-EARNING, so the +0.50 denial
#    (re-arm delayed to W45) prices a real MISS. ─────────────────────
ARC2 = [wk("2026-W37", 3.60, 4.00, state="RISING"),
        wk("2026-W38", 3.30, 3.90, state="RISING"),
        wk("2026-W39", 3.10, 3.95, state="RISING"),
        wk("2026-W40", 3.60, 4.55, src="outcome-fitted",
           fires_i=FO, fires_o=FI, state="RISING"),
        wk("2026-W41", 4.40, 3.35, src="outcome-fitted",
           fires_i=FO, fires_o=FI, state="RISING"),
        wk("2026-W42", 3.45, 4.40, src="outcome-fitted",
           fires_i=FO, fires_o=FI, state="RISING"),
        wk("2026-W43", 3.50, 4.55, src="outcome-fitted",
           fires_i=FO, fires_o=FI, state="RISING"),
        wk("2026-W44", 4.60, 3.60, fires_i=FI, fires_o=FO,
           state="RISING"),
        wk("2026-W45", 4.80, 3.60, fires_i=FI, fires_o=FO,
           state="RISING"),
        wk("2026-W46", 4.70, 3.70, fires_i=FI, fires_o=FO,
           state="RISING")]

# ── arc 3 — the re-arm COSTS (FLAT): the BAD re-arm at pool +0.19.
#    W42-W44 bleed live after the early arm — both dials deny it. ────
ARC3 = [wk("2026-W37", 3.00, 3.40, state="FLAT"),
        wk("2026-W38", 2.90, 3.50, state="FLAT"),
        wk("2026-W39", 2.80, 3.65, state="FLAT"),
        wk("2026-W40", 2.40, 3.55, src="outcome-fitted",
           fires_i=FO, fires_o=FI, state="FLAT"),
        wk("2026-W41", 2.40, 3.45, src="outcome-fitted",
           fires_i=FO, fires_o=FI, state="FLAT"),
        wk("2026-W42", 3.30, 4.55, fires_i=FI, fires_o=FO,
           state="FLAT"),
        wk("2026-W43", 3.20, 4.35, fires_i=FI, fires_o=FO,
           state="FLAT"),
        wk("2026-W44", 3.10, 4.45, fires_i=FI, fires_o=FO,
           state="FLAT")]

# ── arc 4 — FAT POOL / THIN WEEKS (RISING): the re-arm's pool is
#    FAT (+0.7875: a +1.60 week carries the 4-week window) but its
#    trailing winning streak is THIN (ww=2: the −0.10 week breaks
#    the run). The shipped gate re-arms at W43; the pool dials deny
#    on size, the streak dials on duration — and the recovery was
#    real (mild-win W44-W46), so every denial misses. ──────────────
ARC4 = [wk("2026-W37", 3.90, 4.30, state="RISING"),
        wk("2026-W38", 3.95, 4.25, state="RISING"),
        wk("2026-W39", 3.80, 4.40, state="RISING"),
        wk("2026-W40", 3.60, 5.20, src="outcome-fitted",
           fires_i=FO, fires_o=FI, state="RISING"),
        wk("2026-W41", 3.60, 3.50, src="outcome-fitted",
           fires_i=FO, fires_o=FI, state="RISING"),
        wk("2026-W42", 3.60, 4.40, src="outcome-fitted",
           fires_i=FO, fires_o=FI, state="RISING"),
        wk("2026-W43", 3.60, 4.45, src="outcome-fitted",
           fires_i=FO, fires_o=FI, state="RISING"),
        wk("2026-W44", 3.95, 3.75, fires_i=FI, fires_o=FO,
           state="RISING"),
        wk("2026-W45", 4.00, 3.75, fires_i=FI, fires_o=FO,
           state="RISING"),
        wk("2026-W46", 3.90, 3.75, fires_i=FI, fires_o=FO,
           state="RISING")]

# ── arc 5 — THIN POOL / DURABLE RECOVERY (FLAT): the re-arm's pool
#    is THIN (+0.175 — every graduated pool dial denies it) but its
#    streak is DURABLE (ww=4, the streak dials clear it outright).
#    The recovery keeps paying (+0.20..+0.30/week W44-W46), so the
#    pool denial misses big — the shape where a raised pool bar
#    costs the most and the streak dial was right. ─────────────────
ARC5 = [wk("2026-W37", 2.90, 3.40, state="FLAT"),
        wk("2026-W38", 2.95, 3.35, state="FLAT"),
        wk("2026-W39", 2.85, 3.45, state="FLAT"),
        wk("2026-W40", 3.40, 3.50, src="outcome-fitted",
           fires_i=FO, fires_o=FI, state="FLAT"),
        wk("2026-W41", 3.40, 3.55, src="outcome-fitted",
           fires_i=FO, fires_o=FI, state="FLAT"),
        wk("2026-W42", 3.40, 3.60, src="outcome-fitted",
           fires_i=FO, fires_o=FI, state="FLAT"),
        wk("2026-W43", 3.40, 3.65, src="outcome-fitted",
           fires_i=FO, fires_o=FI, state="FLAT"),
        wk("2026-W44", 3.60, 3.40, fires_i=FI, fires_o=FO,
           state="FLAT"),
        wk("2026-W45", 3.65, 3.40, fires_i=FI, fires_o=FO,
           state="FLAT"),
        wk("2026-W46", 3.70, 3.40, fires_i=FI, fires_o=FO,
           state="FLAT")]

# the canonical event lists (the shipped-gate walk must produce these
# exactly — the diag's baseline checks, the suite's, too)
ARCS = [
    {"key": "arc1", "name": "arc 1 — the bench PAYS (OUT)",
     "state": "OUT", "stream": ARC1,
     "canon": [("flip", "2026-W39")], "pools": {}},
    {"key": "arc2", "name": "arc 2 — the bench COSTS (RISING)",
     "state": "RISING", "stream": ARC2,
     "canon": [("flip", "2026-W39"), ("flip_back", "2026-W43")],
     "pools": {"2026-W43": 0.4700}},
    {"key": "arc3", "name": "arc 3 — the re-arm COSTS (FLAT)",
     "state": "FLAT", "stream": ARC3,
     "canon": [("flip", "2026-W39"), ("flip_back", "2026-W41"),
               ("flip", "2026-W44")],
     "pools": {"2026-W41": 0.1900}},
    {"key": "arc4", "name": "arc 4 — FAT POOL / THIN WEEKS (RISING)",
     "state": "RISING", "stream": ARC4,
     "canon": [("flip", "2026-W39"), ("flip_back", "2026-W43")],
     "pools": {"2026-W43": 0.7900}},
    {"key": "arc5", "name": "arc 5 — THIN POOL / DURABLE RECOVERY (FLAT)",
     "state": "FLAT", "stream": ARC5,
     "canon": [("flip", "2026-W39"), ("flip_back", "2026-W43")],
     "pools": {"2026-W43": 0.1700}},
]

# THE FAMILY CANON (the diag's priced dollars, hand-derived): the
# walked family net each graduated threshold prices on each arc —
# the counter-case (arc 2's $180.40 miss), the save (arc 3's
# $307.50), the pass-through (arc 2 @ +0.25: identical walk), and
# arc 1's honest None (no re-arm, nothing to deny).
FAMILY_CANON = {
    "arc1": {0.25: None, 0.50: None},
    "arc2": {0.25: 0.0, 0.50: -180.40},
    "arc3": {0.25: 307.50, 0.50: 307.50},
    # arc4: the fat pool clears both graduated dials — identical
    # walk, nothing to price (the pool family's pass-through)
    "arc4": {0.25: 0.0, 0.50: 0.0},
    # arc5: both pool dials deny the thin +0.175 re-arm and MISS —
    # W44-W46 the fit out-earns the quantile the denial kept shipping
    # (16.40 + 20.50 + 24.60), the disagreement priced
    "arc5": {0.25: -61.50, 0.50: -61.50},
}

# THE FBW FAMILY CANON (the walked FBW candidates' nets per arc;
# machine-verified then hand-checked):
#   arc 2's good re-arm (ww=2): FBW=3 delays it to W44 (-$82.00 = the
#   W44 arm-lag week), FBW=4 to W45 (-$180.40 = W44+W45) — the same
#   dollars the +0.50 pool denial prices, from the OTHER dial.
#   arc 3's bad re-arm (ww=2): both deny outright, the walked stream
#   never re-arms (its W44 re-attempt fails the negative pool) —
#   +$307.50, the pool family's own save.
#   arc 4 (fat pool/thin weeks): the STREAK dial's raise costs —
#   denying the fat-pool re-arm misses W44's +$16.40 (FBW=3 delays
#   the arm to W44) and W44+W45's $36.90 (FBW=4 delays to W45).
#   arc 5 (thin pool/durable recovery): the ww=4 streak clears every
#   FBW candidate — identical walk, nothing to price.
FBW_CANON = {
    "arc1": {3: 0.0, 4: 0.0},
    "arc2": {3: -82.00, 4: -180.40},
    "arc3": {3: 307.50, 4: 307.50},
    "arc4": {3: -16.40, 4: -36.90},
    "arc5": {3: 0.0, 4: 0.0},
}

THRESHOLDS = (0.25, 0.50)
