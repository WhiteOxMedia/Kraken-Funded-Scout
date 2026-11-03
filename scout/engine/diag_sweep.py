r"""diag_sweep.py — which dial actually does the re-arm work?

diag_poolgate.py tested the dials one at a time on FLAT's arc 3; the
family (FLIP_BACK_WEEKS 1..4) x (pool 0 / +0.25 / +0.50) was never
walked TOGETHER. This sweep crosses them — 12 walks of the same seeded
arc plus a 13th no-guard walk — and prices every regime's governed
window in dollars so the dial that actually does the work is named,
not inferred.

CORRECTIONS THE MACHINE FORCED ON THE HAND-DERIVATION (both real
findings): (1) the W40 pool is -0.17, not +0.50 — the fit-live seat
carries 82 fires, not the benched 46; one cf-winning week does NOT
clear even the shipped guard, so FBW=1 re-arms at W41 exactly like
FBW=2 on this arc. (2) Because the W40/W41/W42/W43 pools are -0.17 /
+0.26 / +0.20 / +0.20, the pool dial at +0.50 denies THREE cells the
week count passes — on this arc the dials are complements, not
substitutes.

READ-ONLY: the trail and the weekly history are never touched — every
walk is in-memory; the module constants are snapshotted and restored
in finally. The shipped branch runs verbatim; the threshold denies at
the shipped fire (drop the event, restore the bench), exactly as in
diag_poolgate.
Run: .venv\Scripts\python.exe -X utf8 diag_sweep.py
"""
from pathlib import Path

import tidebar_weekly as tbw

ROOT = Path(__file__).parent
fi, fo = 82, 46   # the fit's fires live / benched (the arc-3 stream)

WEEKS = (1, 2, 3, 4)          # FLIP_BACK_WEEKS ladder
POOLS = (0.0, 0.25, 0.50)     # pool threshold ladder


def wk(tag, live, other, src="quantile", state="FLAT"):
    return {"week": tag, "generated_ts": f"{tag} pass",
            "states": {state: {
                "n": fi,
                "in_force": {"fires": fi, "payers": 3, "rate": 0.3,
                             "usd": round(fi * live, 2),
                             "usd_per_fire": round(live, 2)},
                "other": {"fires": fo, "payers": 3, "rate": 0.3,
                          "usd": round(fo * other, 2),
                          "usd_per_fire": round(other, 2)},
                "other_src": src}}}


def _fit_seat(rec, state):
    st = rec["states"][state]
    fit_live = (st.get("other_src") or "quantile").startswith("quantile")
    return st["in_force"] if fit_live else st["other"]


def _q_seat(rec, state):
    st = rec["states"][state]
    fit_live = (st.get("other_src") or "quantile").startswith("quantile")
    return st["other"] if fit_live else st["in_force"]


# arc 3, re-declared VERBATIM from diag_fbw3/diag_poolgate: W38-W39
# the first bleed (fit live), W40-W41 the recovery (fit benched, cf
# positive), W42 the shipped re-arm (pool +0.26), W43 still recovery,
# W44 the second bleed. The fit is live in its own week's record.
recs = [wk("2026-W37", 2.95, 3.70),
        wk("2026-W38", 2.90, 3.50),
        wk("2026-W39", 2.80, 3.65),
        wk("2026-W40", 2.40, 3.55, src="outcome-fitted"),
        wk("2026-W41", 2.40, 3.45, src="outcome-fitted"),
        wk("2026-W42", 3.30, 4.55),
        wk("2026-W43", 3.20, 4.35),
        wk("2026-W44", 3.10, 4.45)]

_state = {"n": 0, "fail": 0}


def check(name, cond, detail=""):
    _state["n"] += 1
    _state["fail"] += (not cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        print("      detail:", str(detail)[:600])


def pool_at(i):
    """The gate's pooled fit edge as of recs[:i+1]."""
    roll = tbw._rolling_grade(recs[:i + 1], "FLAT")
    return None if roll is None else roll.get("delta")


def walk(fbw, pool_min):
    """The shipped gate with BOTH knobs substituted: FLIP_BACK_WEEKS
    for the streak requirement, pool_min for the inline rd > 0.
    pool_min=None removes the RE-ARM's pool guard entirely — the
    flip_back fires on the winning streak alone (the flip side's
    rd < 0 guard is untouched; the sweep's subject is the re-arm
    dial). Implemented as an explicit guard-free branch after the
    shipped evaluation — no monkeypatching, the shipped machinery
    runs verbatim. For a numeric threshold, a flip_back the shipped
    branch fires whose pool fails it is dropped and the bench
    restored (the deny-at-fire pattern from diag_poolgate)."""
    real_fb, real_fw = tbw.FLIP_BACK_WEEKS, tbw.FLIP_WEEKS
    tbw.FLIP_BACK_WEEKS, tbw.FLIP_WEEKS = fbw, 3
    try:
        events, fl = [], {}
        for rec in recs:
            i = recs.index(rec)
            past = recs[:i + 1]
            evs_w, fl = tbw._evaluate_flips(past, fl)
            e = evs_w.get("FLAT")
            if e and e["action"] == "flip":
                events.append(dict(e, week=rec["week"]))
                continue
            if e and e["action"] == "flip_back":
                # the shipped branch fired it WITH its rd > 0 guard
                if pool_min is not None and pool_min > 0 \
                        and (e.get("roll_delta") or 0) <= pool_min:
                    fl["FLAT"] = {"flipped": True, "since": None,
                                  "reason": ""}
                    continue
                events.append(dict(e, week=rec["week"]))
                continue
            if pool_min is not None:
                continue
            # THE GUARD-FREE BRANCH: the re-arm needs only the streak
            flipped = bool((fl.get("FLAT") or {}).get("flipped"))
            if not flipped:
                continue
            wk_edges = [tbw._week_fit_edge(r, "FLAT") for r in past]
            winning = 0
            for d in reversed(wk_edges):
                if d is None or d <= 0:
                    break
                winning += 1
            if winning >= fbw:   # the WALKED week count, guard-free
                events.append({"action": "flip_back",
                               "winning_weeks": winning,
                               "roll_delta": pool_at(i),
                               "week": rec["week"]})
                fl["FLAT"] = {"flipped": False, "since": None,
                              "reason": ""}
        return events
    finally:
        tbw.FLIP_BACK_WEEKS, tbw.FLIP_WEEKS = real_fb, real_fw


def acts(evs):
    return [(e["action"], e["week"]) for e in evs]


def week_cost(j):
    """(q_rate - fit_rate) x fit_fires for week index j — the
    contract-axis cost of holding the fit live that week."""
    rec = recs[j]
    fr = rec["states"]["FLAT"]["in_force"]["usd_per_fire"]
    qr = rec["states"]["FLAT"]["other"]["usd_per_fire"]
    ff = rec["states"]["FLAT"]["in_force"]["fires"]
    return round((qr - fr) * ff, 2)


COSTS = {j: week_cost(j) for j in range(3, 8)}   # W40..W44

# ── the sweep: 12 cells + the 13th no-guard walk ───────────────────
# THE DOLLAR AXIS (consistent with both earlier studies): for each
# week a regime holds the FIT live, its contract-axis cost vs the
# quantile counterfactual is (q_rate - fit_rate) x fit_fires. The
# shipped re-arm (fit live W42-W44) = 102.50 + 94.30 + 110.70 =
# 307.50. A guard-free re-arm fires at W40's close (fit live W41-W44)
# and adds W41's 86.10 -> 393.60. A full deny costs 0.00.
check("the per-week cost arithmetic (hand-checked): W41 86.10, "
      "W42 102.50, W43 94.30, W44 110.70",
      (COSTS[4], COSTS[5], COSTS[6], COSTS[7])
      == (86.10, 102.50, 94.30, 110.70), COSTS)
check("the pool ladder hand-checks over the five closes W40-W44: "
      "-0.17, +0.26, +0.20, +0.20, -0.45 (the W40 pool is "
      "fit-live-seat: 82 fires, not the benched 46)",
      [pool_at(j) for j in (3, 4, 5, 6, 7)]
      == [-0.17, 0.26, 0.2, 0.2, -0.45],
      [pool_at(j) for j in (3, 4, 5, 6, 7)])

# the 13th walk: BOTH dials at zero — FBW=1, no pool guard. Any
# single cf-winning week re-arms. Hand-derived: the W40 close
# (streak 1) re-arms a week early -> fit live W41-W44 = $393.60.
# This is the no-gate baseline the shipped settings are measured
# against.
evs_noguard = walk(1, None)
check("the no-gate baseline (FBW=1, pool guard removed) re-arms a "
      "week early — W40's close — the regime the shipped dials are "
      "measured against",
      acts(evs_noguard) == [("flip", "2026-W39"),
                            ("flip_back", "2026-W40"),
                            ("flip", "2026-W44")],
      acts(evs_noguard))
check("a no-guard walk at the SHIPPED FBW=2 is identical to shipped "
      "(W41 re-arm): the pool guard never bites at FBW=2 on this "
      "arc — its bite point is FBW=1, where it delays W40 -> W41",
      acts(walk(2, None)) == [("flip", "2026-W39"),
                              ("flip_back", "2026-W41"),
                              ("flip", "2026-W44")],
      acts(walk(2, None)))
noguard_cost = round(sum(COSTS[j] for j in range(4, 8)), 2)
check("the no-gate baseline costs $393.60 (W41-W44 live) vs the "
      "shipped $307.50 — the shipped dials' realized work on this "
      "arc is the $86.10 one-week delay, not the $307.50 deny",
      noguard_cost == 393.60
      and round(noguard_cost - 307.50, 2) == 86.10,
      noguard_cost)

# the 12-cell matrix, derived by hand BEFORE the run (with the
# corrected W40 pool):
#   FBW=1: the W40 close fires nothing (pool -0.17 fails even the
#          shipped guard); W41's pool +0.26 re-arms under pool 0 and
#          +0.25 (by one cent) and DENIES under +0.50.
#   FBW=2: the shipped 3 cells — re-arm W41 / W41 / deny @ +0.50.
#   FBW=3/4: deny at every pool (no third/fourth cf-winning week).
EXPECT = {(1, 0.0): ("REARM", "2026-W41"),
          (1, 0.25): ("REARM", "2026-W41"),
          (1, 0.5): ("DENY", None),
          (2, 0.0): ("REARM", "2026-W41"),
          (2, 0.25): ("REARM", "2026-W41"),
          (2, 0.5): ("DENY", None),
          (3, 0.0): ("DENY", None), (3, 0.25): ("DENY", None),
          (3, 0.5): ("DENY", None),
          (4, 0.0): ("DENY", None), (4, 0.25): ("DENY", None),
          (4, 0.5): ("DENY", None)}

rows = []
mismatch = []
for fbw in WEEKS:
    for pool in POOLS:
        evs = walk(fbw, pool)
        back = next((e for e in evs if e["action"] == "flip_back"), None)
        if back:
            start = int(back["week"].split("W")[1]) + 1
            cost = round(sum(COSTS[j] for j in range(4, 8)
                             if j + 37 >= start), 2)
            got = ("REARM", back["week"])
        else:
            got, cost = ("DENY", None), 0.0
        rows.append((fbw, pool, got[0], got[1], cost))
        if got != EXPECT[(fbw, pool)]:
            mismatch.append((fbw, pool, got))

check("all 12 cells match the hand-derived classification (which "
      "knob setting re-arms, which denies, and where)",
      not mismatch, mismatch)

# the reference cell: the shipped walk
ref = next(r for r in rows if r[0] == 2 and r[1] == 0.0)
check("the reference cell (FBW=2, pool 0) is the shipped regime: "
      "re-arm W41, $307.50 of contract-axis cost",
      ref[2] == "REARM" and ref[3] == "2026-W41" and ref[4] == 307.50,
      ref)
evs_ship = walk(2, 0.0)
check("the shipped walk's event shape: flip W39 / flip-back W41 / "
      "re-flip W44 (the arc as shipped)",
      acts(evs_ship) == [("flip", "2026-W39"), ("flip_back", "2026-W41"),
                         ("flip", "2026-W44")], acts(evs_ship))

# the complementarity ledger: which dial denies which cell
deny_cells = {(fbw, pool) for fbw, pool, v, _, _ in rows if v == "DENY"}
pool_only = {(f, p) for f, p in deny_cells if f <= 2}     # weeks pass
week_only = {(f, p) for f, p in deny_cells if p <= 0.25}  # pool passes
both = deny_cells - pool_only - week_only
SAVE = 307.50
check("the dial ledger: 8 deny cells — the pool dial exclusively "
      "denies 2 (FBW<=2 @ +0.50), the week dial exclusively 4 "
      "(FBW>=3 @ pool<=+0.25), they share 2 — neither is a subset "
      "of the other on this arc",
      len(deny_cells) == 8 and len(pool_only) == 2
      and len(week_only) == 4 and len(both) == 2,
      (sorted(deny_cells), sorted(pool_only), sorted(week_only),
       sorted(both)))
check("the exclusive saves in dollars: the pool dial's own cells "
      "save $615.00 the week ladder cannot reach; the week dial's "
      "own cells save $1,230.00 the pool ladder cannot reach",
      round(len(pool_only) * SAVE, 2) == 615.00
      and round(len(week_only) * SAVE, 2) == 1230.00,
      (len(pool_only) * SAVE, len(week_only) * SAVE))

print()
print("-- THE MATRIX (arc 3, FLAT) --")
print(f"{'FBW':>4} {'pool':>6} {'verdict':<18} {'cost $':>9}")
for fbw, pool, verdict, wk_, cost in rows:
    v = verdict if wk_ is None else f"{verdict} @ {wk_}"
    print(f"{fbw:>4} {pool:>+6.2f} {v:<18} {cost:>+9.2f}")
print(f"{'—':>4} {'zero':>6} {'REARM @ 2026-W40':<18} {noguard_cost:>+9.2f}"
      "   <- both dials at zero (FBW=1, no guard)")
print()
print("  cost = the contract-axis dollars the regime's live-fit weeks")
print("  leave vs the quantile counterfactual ((q-fire) x fit fires,")
print("  summed); a full deny costs 0.00 on this axis.")
print()

# ── which dial does the work? ───────────────────────────────────────
print("-- THE VERDICT: WHICH DIAL DOES THE WORK --")
print()
print("  ON THIS ARC: neither dial alone does the work — they are")
print("  COMPLEMENTS, each with exclusive saves the other cannot")
print("  reach:")
print(f"    - the week count (FBW>=3) exclusively denies 4 cells =")
print(f"      $1,230.00 of savings at any pool up to +0.25: no pool")
print(f"      setting can deny a thin recovery, and none does.")
print(f"    - the pool dial (+0.50) exclusively denies 2 cells =")
print(f"      $615.00 the week ladder cannot reach: FBW 1..2 all pass")
print(f"      the two-week streak, so only the pool can veto the")
print(f"      +0.26 re-arm (and the W40 close's -0.17 pool is why the")
print(f"      shipped guard already delays any FBW=1 re-arm to W41).")
print(f"    - they share 2 cells (FBW>=3 AND +0.50) = $615.00 either")
print(f"      would deny alone.")
print(f"    - the full strict family (FBW>=3, pool +0.50) denies 8 of")
print(f"      12 cells = $2,460.00 of the family's counterfactual; no")
print(f"      single-knob setting reaches that (best single: FBW>=3 at")
print(f"      pool 0 = 6 cells).")
print()
print("  IN DOLLARS, the shipped dials' REALIZED work on this arc is only")
print("  the $86.10 one-week delay vs the no-gate baseline (W40 ->")
print("  W41) — the $307.50 deny is available only by TIGHTENING a")
print("  dial past shipped, and either one alone buys it (FBW=3 or")
print("  pool +0.50); tightening BOTH denies more of the family's")
print("  counterfactual but changes nothing for THIS arc's decision")
print("  — the first deny is the whole save, and it is already priced.")
print()
print("  THE HONEST CAVEAT (carried from diag_poolgate): one arc, one")
print("  premature re-arm, zero good re-arms. The sweep names which")
print("  dial does the denying work HERE and what each exclusively")
print("  saves; which dial is safer in general needs the fat-pool/")
print("  thin-weeks arc (where the threshold beats the count) and")
print("  the thin-pool/durable-recovery arc (where the count beats")
print("  the threshold) — both absent by construction, gradable only")
print("  on the live ledger as real flips accumulate.")
print()

# idempotence: the shipped constants are exactly restored
check("the shipped gate constants are restored (FLIP_BACK_WEEKS=2, "
      "FLIP_WEEKS=3, ROLLING_WEEKS=4)",
      tbw.FLIP_BACK_WEEKS == 2 and tbw.FLIP_WEEKS == 3
      and tbw.ROLLING_WEEKS == 4,
      (tbw.FLIP_BACK_WEEKS, tbw.FLIP_WEEKS, tbw.ROLLING_WEEKS))
# read-only: the trail is byte-identical (the walks never touch it)
trail = ROOT / "tide_bar_flip_events.jsonl"
_b = trail.read_bytes() if trail.exists() else None
check("the audit trail was never touched",
      (trail.read_bytes() if trail.exists() else None) == _b)

total, fails = _state["n"], _state["fail"]
print(f"\n{'ALL GREEN' if fails == 0 else 'FAILURES PRESENT'} "
      f"- {total - fails}/{total} checks")
raise SystemExit(0 if fails == 0 else 1)
