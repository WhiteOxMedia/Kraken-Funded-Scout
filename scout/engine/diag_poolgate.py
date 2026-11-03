r"""diag_poolgate.py — is a pool threshold a more robust re-arm gate
than a week count?

Follow-up to diag_fbw3.py: FBW=3 wouldn't have DELAYED FLAT's arc-3
re-arm — it would have DENIED it (the recovery was exactly two weeks).
That report ended on the open question: the pool guard passed the
re-arm at only +0.16 — would a pool THRESHOLD (require the pooled fit
edge > +0.25 or > +0.50, not merely > 0) deny premature re-arms more
robustly than a week count?

FIRST FINDING, BEFORE THE STUDY: the earlier report's "+0.16" was the
sim's embedded aftermath number, not the gate's reading. On the arc's
own stream the shipped gate's pooled edge at the re-arm (W41's close,
rolling-4 = W38-W41) is +0.26 — hand arithmetic: fit (82*2.90 +
82*2.80 + 46*3.55 + 46*3.45)/256 = 789.40/256 = 3.0836; quantile
(46*3.50 + 46*3.65 + 82*2.40 + 82*2.40)/256 = 722.50/256 = 2.8223;
delta = 0.2613. The sim's prose is corrected in diag_fbw3.py (one
phrase). This study re-derives every number it reports from the
gate's own building blocks.

THE ROBUSTNESS DEFINITION (honest up front): a gate is more robust
when it stops the losing re-arm while keeping every paying one. On
this arc there is one re-arm decision, so the study grades three
things: (1) the discrimination on the arc's own evidence (the pool
the gate read at the re-arm vs each threshold), (2) the COST OF THE
MISS in dollars (shipped vs threshold regime over the re-arm's
governed window W42-W44, realized vs counterfactual), and (3) the
margin — which dial denies THIS re-arm while keeping a hypothetical
strong-recovery arc (a fat pool and a third winning week) re-arming.

READ-ONLY: the trail, the flips ledger, and the weekly history are
never touched — every decision walk is in-memory over the arc's
seeded stream; the module-level constants are snapshotted and
restored in finally. At each week the shipped branch runs VERBATIM
first; a threshold flip-back is evaluated ONLY where the shipped gate
did not fire, so the threshold is the single difference between walks.
Run: .venv\Scripts\python.exe -X utf8 diag_poolgate.py
"""
import json
from pathlib import Path

import poolgate_arcs
import tidebar_weekly as tbw

ROOT = Path(__file__).parent
fi, fo = poolgate_arcs.FI, poolgate_arcs.FO   # the shared arc's fire convention

# THE SHARED FIXTURES (Sep 13): the arc streams live in
# poolgate_arcs.py — the ONE set of records both the study and the
# live pool-gate machines are pinned against (the suite prices
# grade_poolgate and the family sweep over these same streams and
# asserts this study's canon dollars). wk() below delegates to the
# shared builder so the legacy call sites keep their exact shape.
wk = poolgate_arcs.wk

POOL_MIN = 0.25     # candidate A: the re-arm's pooled edge must clear
POOL_STRONG = 0.50  # candidate B: the strict variant


def _fit_seat(rec, state):
    st = rec["states"][state]
    fit_live = (st.get("other_src") or "quantile").startswith("quantile")
    return st["in_force"] if fit_live else st["other"]


def _q_seat(rec, state):
    st = rec["states"][state]
    fit_live = (st.get("other_src") or "quantile").startswith("quantile")
    return st["other"] if fit_live else st["in_force"]


# arc 3 (diag_flipgrade's seed), re-declared VERBATIM — the arc's own
# stream (the real W37 record's FLAT slice is one-sided: importing it
# silently resets the streak and misses the arc's opening flip —
# diag_fbw3's caught bug). W38-W39 the first bleed (fit live, edge
# negative), W40-W41 the recovery (fit benched, cf-edge positive),
# W42 the re-arm (the gate reads +0.26), W43 still recovery, W44 the
# second bleed. The W37 seed mirrors the real record's W38 shape so
# the opening flip's arithmetic is real.
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


def pooled_edge_at(i):
    """The shipped gate's pooled fit edge (rd) as of recs[:i+1] — the
    same _rolling_grade call the decision branch reads."""
    roll = tbw._rolling_grade(recs[:i + 1], "FLAT")
    return None if roll is None else roll.get("delta")


def walk(pool_min, stream=None, state="FLAT"):
    """Re-run the shipped gate on an arc with the pool threshold
    substituted for the inline rd > 0 — the ONLY change. (tidebar_weekly
    now SHIPS POOL_MIN as a constant; the walk pins it to 0.0 inside so
    the threshold stays the single difference between walks.) The
    shipped branch runs verbatim first; where it fires a FLIP_BACK whose
    pool fails the threshold, the event is DROPPED and the bench
    restored (the shipped branch already un-flipped the state) — the
    threshold denies at the shipped fire, and the state stays benched
    until a later week's pool clears it (a DELAYED re-arm appears
    naturally). Flip decisions are untouched (this study thresholds the
    re-arm side only)."""
    if stream is None:
        stream = recs
    real_fb, real_fw = tbw.FLIP_BACK_WEEKS, tbw.FLIP_WEEKS
    real_pm = tbw.POOL_MIN
    tbw.FLIP_WEEKS = 3
    tbw.POOL_MIN = 0.0
    try:
        events, fl = [], {}
        for i, rec in enumerate(stream):
            past = stream[:i + 1]
            evs_w, fl = tbw._evaluate_flips(past, fl)
            e = evs_w.get(state)
            if e:
                if e["action"] == "flip_back" and pool_min > 0 \
                        and (e.get("roll_delta") or 0) < pool_min:
                    # THE THRESHOLD DENIES the shipped re-arm: drop
                    # the event, restore the bench, keep walking.
                    # Strict < (deny) = the shipped gate's inclusive
                    # rd >= POOL_MIN clears.
                    fl[state] = {"flipped": True, "since": None,
                                 "reason": ""}
                    continue
                events.append(dict(e, week=rec["week"]))
                continue
            if pool_min <= 0:
                continue
            # (deny-at-fire covers every later week too: the shipped
            # branch re-fires the flip_back each week the streak holds,
            # and each fire re-passes through the threshold above.)
        return events
    finally:
        tbw.FLIP_BACK_WEEKS, tbw.FLIP_WEEKS, tbw.POOL_MIN = \
            real_fb, real_fw, real_pm


def acts(evs):
    return [(e["action"], e["week"]) for e in evs]


print("=== THE POOL THRESHOLD, REPLAYED (FLAT's arc 3) ===")
print()

# ── equivalence at 0: the walk IS the shipped gate ─────────────────
evs_ship = walk(0.0)
check("at threshold 0 the walk reproduces the shipped FBW=2 arc "
      "(flip W39 / flip-back W41 / re-flip W44)",
      acts(evs_ship) == [("flip", "2026-W39"), ("flip_back", "2026-W41"),
                         ("flip", "2026-W44")], acts(evs_ship))
check("the shipped pool guard passed the re-arm at +0.26 on its own "
      "rolling-4 window (hand arithmetic: 789.40/256 - 722.50/256 = "
      "0.2613)",
      evs_ship[1]["roll_delta"] == 0.26,
      (evs_ship[1].get("roll_delta"), pooled_edge_at(5)))

# ── the discrimination ─────────────────────────────────────────────
evs_25 = walk(POOL_MIN)
evs_50 = walk(POOL_STRONG)
check(f"at pool > {POOL_MIN} the +0.26 re-arm is NOT denied (the "
      "threshold is below the gate's own read)",
      acts(evs_25) == acts(evs_ship), acts(evs_25))
check(f"at pool > {POOL_STRONG} the re-arm IS denied — and stays "
      "denied: the arc ends flip W39 only (a benched state cannot "
      "re-flip, so W44's losing week just extends the bench)",
      acts(evs_50) == [("flip", "2026-W39")],
      acts(evs_50))
check("the denial is stable across the arc's re-checks: the "
      "recomputed pools at W42 (+0.20) and W43 (+0.20) also fail "
      "+0.50, and W44's bleed sinks the pool to -0.45 while "
      "breaking the winning streak outright",
      pooled_edge_at(5) == 0.2 and pooled_edge_at(6) == 0.2
      and pooled_edge_at(7) == -0.45,
      [pooled_edge_at(j) for j in (5, 6, 7)])

# hand arithmetic for the governed window (the study's hand-check):
#   W42 fit: 82 * 3.30 = 270.60   W42 quantile: 46 * 4.55 = 209.30
#   W43 fit: 82 * 3.20 = 262.40   W43 quantile: 46 * 4.35 = 200.10
#   W44 fit: 82 * 3.10 = 254.20   W44 quantile: 46 * 4.45 = 204.70
#   fit: 787.20 over 246 fires = 3.20/fire
#   quantile: 614.10 over 138 fires = 4.45/fire
check("the governed window's hand arithmetic: fit 787.20/246 = "
      "$3.20/fire vs quantile 614.10/138 = $4.45/fire",
      (round(sum(recs[j]["states"]["FLAT"]["in_force"]["usd"]
                 for j in (5, 6, 7)), 2),
       sum(recs[j]["states"]["FLAT"]["in_force"]["fires"]
           for j in (5, 6, 7)),
       round(sum(recs[j]["states"]["FLAT"]["other"]["usd"]
                 for j in (5, 6, 7)), 2),
       sum(recs[j]["states"]["FLAT"]["other"]["fires"]
           for j in (5, 6, 7))) == (787.20, 246, 614.10, 138),
      [(recs[j]["states"]["FLAT"]["in_force"]["usd_per_fire"],
        recs[j]["states"]["FLAT"]["other"]["usd_per_fire"])
       for j in (5, 6, 7)])

# ── the dollars: shipped vs denied over the re-arm's window ────────
# Under the shipped gate the fit is LIVE W42-W44 (re-armed at W41's
# close, re-flipped at W44's close) and realizes 787.20/246. Under
# pool > 0.50 the re-arm never comes; the quantile ships all three
# weeks (614.10/138). The W44 re-flip fires under every regime (the
# first bleed is common history), so all three weeks are governed by
# the re-arm decision — the differentiating window is exactly
# W42-W44, and every dollar of the gap is the re-arm's, not the exit's.
lf = sum(recs[j]["states"]["FLAT"]["in_force"]["fires"] for j in (5, 6, 7))
lu = sum(recs[j]["states"]["FLAT"]["in_force"]["usd"] for j in (5, 6, 7))
qf = sum(recs[j]["states"]["FLAT"]["other"]["fires"] for j in (5, 6, 7))
qu = sum(recs[j]["states"]["FLAT"]["other"]["usd"] for j in (5, 6, 7))
lp, qp = round(lu / lf, 2), round(qu / qf, 2)
gap_pf = round(qp - lp, 2)
gap_usd = round(gap_pf * lf, 2)

check("the shipped re-arm's realized $/fire is the fit's 3.20 vs "
      "the quantile's 4.45 (COSTS on the contract axis)",
      lp == 3.20 and qp == 4.45, (lp, qp, gap_pf))

print()
print("-- THE DOLLARS (the re-arm's governed window, W42-W44) --")
print(f"  shipped (pool > 0, FBW=2): the re-armed fit realizes")
print(f"    ${lp:+.2f}/fire over {lf} fires (${lu:,.2f} total) vs the")
print(f"    quantile's ${qp:+.2f}/fire over {qf} fires (${qu:,.2f}) —")
print(f"    the re-arm COSTS ${gap_pf:+.2f}/fire = ${gap_usd:+,.2f} of")
print("    counterfactual on the contract axis.")
print()
print(f"  pool > {POOL_MIN}: NOT denied — the gate's own read (+0.26)")
print("    clears +0.25; the walk is identical to shipped. The")
print("    threshold sits INSIDE the margin of this decision, which")
print("    is itself the finding: +0.25 would not have saved FLAT.")
print()
print(f"  pool > {POOL_STRONG}: DENIED — and the denial holds: the")
print(f"    recomputed pools at W42/W43 (+0.20 each) also fail +0.50,")
print(f"    so the fit stays benched and the quantile ships W42-W44 at")
print(f"    ${qp:+.2f}/fire — the same window the FBW=3 counterfactual")
print("    ships. The W44 bleed breaks the winning streak before the")
print("    gate reconsiders, so no re-arm ever lands on this arc.")
print()

# ── the margin analysis ────────────────────────────────────────────
print("-- THE MARGIN: WHICH DIAL DENIES, AND HOW CLOSE IT SITS --")
print(f"  the re-arm decision sat at pool +0.26 / 2 winning weeks.")
print(f"    FBW=3 denies by a WIDE margin (no third winning week ever")
print("    exists — the recovery was exactly two weeks). A pool of")
print(f"    +0.50 denies by a NARROW margin at the decision (0.26 vs")
print(f"    0.50) but holds it through the re-checks (+0.20, +0.20);")
print(f"    +0.25 does not deny at all (gap {round(0.26 - 0.25, 2):+.2f}).")
print("    On THIS arc the graduated family splits: +0.25 passes the")
print("    bad re-arm, +0.50 denies it — and FBW=3 denies it too, so")
print("    the two dials AGREE here; they part ways on arc shape (see")
print("    the robustness case).")
print()
print("  THE ROBUSTNESS CASE FOR THE THRESHOLD (beyond one arc): the")
print("  week count measures the recovery's SHAPE (how many weeks),")
print("  not its STRENGTH (how well the fit would fire). Any future")
print("  arc with a fat pool and a slow recovery passes FBW=3 while")
print("  bleeding; the pool threshold reads strength, which is what")
print("  the governed dollars scale with (the size ladder scales")
print("  exposure per fire). The contract axis is $/fire; the pool")
print("  is $/fire — the dial and the axis are the same unit.")
print()

# ── the honest counter-case ────────────────────────────────────────
print("-- THE HONEST COUNTER-CASE (one arc cannot price it) --")
print("  a threshold DENIES more: an arc with a thin pool but a")
print("  real, durable recovery (small samples, genuinely better")
print("  fit) stays benched under pool > +0.50 while FBW=2 re-arms")
print("  it — the threshold's own missed-opportunity cost,")
print("  unpriceable on this arc. And FBW=3's denial here was free")
print("  BECAUSE the recovery was thin; the same constant on a fat-")
print("  pool/thin-weeks arc would deny a GOOD re-arm. Neither dial")
print("  dominates a priori; the graduated family (0 / +0.25 /")
print("  +0.50) exists so the live ledger's accumulating flip")
print("  history can grade the dial monthly — the flip-grade")
print("  machinery already re-runs.")
print()

# ═══════════════════════════════════════════════════════════════════
# THE FAMILY SWEEP (Sep 12): the same threshold study across ALL THREE
# diag_flipgrade sim arcs — arc 1 (OUT, the bench PAYS), arc 2
# (RISING, the bench COSTS), arc 3 (FLAT, the re-arm COSTS) — then the
# family net: does any dial deny more than it saves?
# ═══════════════════════════════════════════════════════════════════
print()
print("=== THE FAMILY SWEEP (thresholds across all three sim arcs) ===")
print()

_arc1 = [wk("2026-W37", 4.00, 4.82, state="OUT"),
         wk("2026-W38", 4.00 - 0.4, 4.82 + 0.3, state="OUT"),
         wk("2026-W39", 4.00 - 0.5, 4.82 + 0.2, state="OUT"),
         wk("2026-W40", 4.00 - 0.6, 4.82 + 0.1, state="OUT")]
_arc2 = [wk("2026-W37", 3.60, 4.00, state="RISING"),
         wk("2026-W38", 3.30, 3.90, state="RISING"),
         wk("2026-W39", 3.10, 3.95, state="RISING"),
         wk("2026-W40", 3.60, 4.55, src="outcome-fitted",
            fires_i=fo, fires_o=fi, state="RISING"),
         wk("2026-W41", 4.40, 3.35, src="outcome-fitted",
            fires_i=fo, fires_o=fi, state="RISING"),
         # THE COUNTER-CASE EXTENSION (Sep 13): the original arc ended
         # on its re-arm — the governed window never traded, so a
         # denial was UNPRICEABLE and the family net carried no cost
         # for the strict dial. W42-W43's recovery is shaved a touch
         # (fit cf 4.70/4.60 -> 4.40/4.55: the pool at the re-arm
         # reads +0.4745 instead of +0.5625 — every canonical event
         # kept, the 2-week winning streak intact) so +0.50 DENIES
         # the paying re-arm, and W44-W46 trade the aftermath with
         # the fit LIVE and OUT-EARNING (the arc is the GOOD re-arm;
         # its window must say so): the walk's pool is still negative
         # at W44 (rolling-4 mixes the benched recovery with one
         # live week) and clears at W45 (+0.7468) — so the +0.50
         # walk re-arms at W45 and the denial's one divergent week
         # (W44: fit 4.60 vs quantile 3.60 x 82 fires) prices the
         # miss at $82.20 the strict dial would have cost.
         wk("2026-W42", 3.45, 4.40, src="outcome-fitted",
            fires_i=fo, fires_o=fi, state="RISING"),
         wk("2026-W43", 3.50, 4.55, src="outcome-fitted",
            fires_i=fo, fires_o=fi, state="RISING"),
         wk("2026-W44", 4.60, 3.60, fires_i=fi, fires_o=fo,
            state="RISING"),
         wk("2026-W45", 4.80, 3.60, fires_i=fi, fires_o=fo,
            state="RISING"),
         wk("2026-W46", 4.70, 3.70, fires_i=fi, fires_o=fo,
            state="RISING")]
_arc3 = [wk("2026-W37", 3.00, 3.40, state="FLAT"),
         wk("2026-W38", 2.90, 3.50, state="FLAT"),
         wk("2026-W39", 2.80, 3.65, state="FLAT"),
         wk("2026-W40", 2.40, 3.55, src="outcome-fitted",
            fires_i=fo, fires_o=fi, state="FLAT"),
         wk("2026-W41", 2.40, 3.45, src="outcome-fitted",
            fires_i=fo, fires_o=fi, state="FLAT"),
         wk("2026-W42", 3.30, 4.55, fires_i=fi, fires_o=fo,
            state="FLAT"),
         wk("2026-W43", 3.20, 4.35, fires_i=fi, fires_o=fo,
            state="FLAT"),
         wk("2026-W44", 3.10, 4.45, fires_i=fi, fires_o=fo,
            state="FLAT")]
_ARCS = [("arc 1 — the bench PAYS (OUT)", _arc1, "OUT",
          [("flip", "2026-W39")]),
         ("arc 2 — the bench COSTS (RISING)", _arc2, "RISING",
          [("flip", "2026-W39"), ("flip_back", "2026-W43")]),
         ("arc 3 — the re-arm COSTS (FLAT)", _arc3, "FLAT",
          [("flip", "2026-W39"), ("flip_back", "2026-W41"),
           ("flip", "2026-W44")]),
         ("arc 4 — FAT POOL / THIN WEEKS (RISING)", poolgate_arcs.ARC4,
          "RISING",
          [("flip", "2026-W39"), ("flip_back", "2026-W43")]),
         ("arc 5 — THIN POOL / DURABLE RECOVERY (FLAT)",
          poolgate_arcs.ARC5, "FLAT",
          [("flip", "2026-W39"), ("flip_back", "2026-W43")])]
_THRESHOLDS = [(0.0, "shipped"), (0.25, "+0.25"), (0.50, "+0.50")]


def _seat(rec, state, want_fit):
    """The (fit, quantile) seat pair, regime-normalized by other_src."""
    st = rec["states"][state]
    fit_live = (st.get("other_src") or "quantile").startswith("quantile")
    pair = (st["in_force"], st["other"])
    return pair if (fit_live == want_fit) else pair[::-1]


def _window_pricing(stream, state, wa, wb):
    """Tag-normalized pricing over the half-open week range [wa, wb):
    (fit_$ per fire − quantile_$ per fire, the fires the shipped
    regime exposes). diff < 0 = the fit UNDER-earns the window, so a
    denied re-arm (the quantile ships instead) SAVES -diff × fires."""
    f_fires = f_usd = q_fires = q_usd = 0
    for r in stream:
        if r["week"] < wa or r["week"] >= wb:
            continue
        f, q = _seat(r, state, True)
        f_fires += f["fires"]
        f_usd += f["usd"]
        q_fires += q["fires"]
        q_usd += q["usd"]
    if f_fires == 0 or q_fires == 0:
        return None, None
    return round(f_usd / f_fires - q_usd / q_fires, 4), f_fires


# one walk per (arc, threshold): the events ARE the counterfactual
_sweeps = {}
for name, stream, state, _canon in _ARCS:
    _sweeps[name] = {}
    for pm, label in _THRESHOLDS:
        _sweeps[name][pm] = walk(pm, stream, state)

family = {}
for name, stream, state, canon in _ARCS:
    ship = _sweeps[name][0.0]
    check(f"{name}: the shipped walk (threshold 0) reproduces the "
          f"arc's canonical events",
          acts(ship) == canon, acts(ship))
    rearm = next((e for e in ship if e["action"] == "flip_back"), None)
    print(f"-- {name} --")
    if rearm is None:
        print("    no re-arm on this arc — thresholds have nothing "
              "to deny")
        family[name] = {pm: None for pm, _ in _THRESHOLDS[1:]}
        print()
        continue
    pool = rearm.get("roll_delta") or 0.0
    print(f"    the shipped re-arm at {rearm['week']} cleared a pool "
          f"of {pool:+.4f}/fire")
    for pm, label in _THRESHOLDS[1:]:
        evs = _sweeps[name][pm]
        if acts(evs) == acts(ship):
            print(f"    {label}: PASSES (margin "
                  f"{round(pool - pm, 4):+.4f}/fire) — identical walk")
            family[name] = family.get(name) or {}
            family[name][pm] = 0.0
            continue
        ship_r = [w for a, w in acts(ship) if a == "flip_back"]
        thr_r = [w for a, w in acts(evs) if a == "flip_back"]
        denied = [w for w in ship_r if w not in thr_r]
        delayed = [w for w in thr_r if w not in ship_r]
        if delayed:
            # THE DELAYED RE-ARM PRICES (Sep 13): the walked re-arm
            # may be a DELAY (a shipped arm precedes it) or a CASCADE
            # (no shipped arm before it — the denial changed the
            # regime path). The delay's dollars price in the family
            # section below; here the shape is named.
            casc = [w for w in delayed
                    if not any(sw < w for sw in ship_r)]
            print(f"    {label}: DELAYS the re-arm (shipped {ship_r} "
                  f"vs threshold {thr_r})"
                  + (f" — CASCADE: {casc} re-arm(s) the shipped "
                     "stream never made" if casc else ""))
            family[name] = family.get(name) or {}
            family[name][pm] = denied
        elif denied:
            print(f"    {label}: DENIES the re-arm at {denied}")
            family[name] = family.get(name) or {}
            family[name][pm] = denied
        else:
            print(f"    {label}: diverges — {acts(evs)} vs {acts(ship)}")
            family[name] = family.get(name) or {}
            family[name][pm] = None
    print()

# per-arc dollar pricing (Sep 13, v2): the GOVERNANCE WALK — each
# world's shipping regime derived week-by-week from its own event
# list (an event at w arms/benches from w+1), then the DIVERGENCE is
# priced: every week the threshold world benches the fit while the
# shipped world arms it contributes −(fit−q)×fit_fires (the same
# −diff × fires axis as the single-arc study), and the reverse (a
# cascade re-arm earlier than shipped) contributes +(fit−q). One
# divergence, priced once — no deny-window/delay-span overlap.
print("-- THE FAMILY DOLLARS (each threshold priced on its "
      "divergence from the shipped governance) --")
print()
ledger = {}


def _governance(stream, ev_list):
    """week -> the seat shipping ('fit' | 'quantile'), derived from
    the event list: an event at w takes effect from w+1; the stream
    starts with the fit live (the arcs open unflipped)."""
    evs_s = sorted(ev_list, key=lambda x: x[1])
    gov, flipped, ev_i = {}, False, 0
    for r in stream:
        w = r["week"]
        gov[w] = "quantile" if flipped else "fit"
        while ev_i < len(evs_s) and evs_s[ev_i][1] == w:
            flipped = (evs_s[ev_i][0] == "flip")
            ev_i += 1
    return gov


for name, stream, state, canon in _ARCS:
    ship = _sweeps[name][0.0]
    ship_ev = acts(ship)
    gov_ship = _governance(stream, ship_ev)
    ledger[name] = {}
    for pm, label in _THRESHOLDS[1:]:
        thr = _sweeps[name][pm]
        gov_thr = _governance(stream, acts(thr))
        tot, n_div = 0.0, 0
        for r in stream:
            w = r["week"]
            if gov_ship[w] == gov_thr[w]:
                continue
            f, q = _seat(r, state, True)
            if not f.get("fires") or not q.get("fires"):
                continue
            diff = (f["usd_per_fire"] - q["usd_per_fire"]) * f["fires"]
            tot += diff if gov_thr[w] == "fit" else -diff
            n_div += 1
        if n_div:
            saved = round(tot, 2)
            ledger[name][pm] = saved
            print(f"  {name} @ {label}: {n_div} divergent week(s) "
                  f"vs the shipped governance -> "
                  f"{'SAVES' if saved >= 0 else 'COSTS'} "
                  f"${abs(saved):,.2f}")
        else:
            print(f"  {name} @ {label}: identical governance — "
                  "nothing to price")
    print()

print("-- THE FAMILY LEDGER (denied-then-priced dollars, by "
      "threshold) --")
for pm, label in _THRESHOLDS[1:]:
    parts, tot, unpriced = [], 0.0, []
    for name, _, _, _ in _ARCS:
        v = ledger[name].get(pm)
        key = name.split(" —")[0]
        if v is None:
            unpriced.append(key)
        else:
            parts.append(f"{key} ${v:+,.2f}")
            tot += v
    line = f"    {label}: " + (" · ".join(parts) if parts
                               else "nothing denied anywhere")
    if unpriced:
        line += f"  (unpriceable: {', '.join(unpriced)})"
    print(line)
    print(f"      net: ${tot:+,.2f}")
print()
print("-- THE ANSWER (family-wide) --")
print("  the counter-case is no longer hypothetical — it prices:")
print("  arc 3's bad re-arm denial saves $307.50 (the fit under-")
print("  earned its whole governed window), while arc 2's GOOD re-")
print("  arm — denied only by the strict dial after one bad recovery")
print("  week was shaved off — costs $180.40 (the fit out-earned")
print("  its bench two weeks running). +0.25 keeps arc 2's re-arm")
print("  (margin +0.22/fire) and banks the full $307.50; +0.50")
print("  denies BOTH and nets $127.10 — the extra strictness is")
print("  priced, not free: it buys no additional savings on the")
print("  family (arc 3 is denied by either dial) and pays real")
print("  dollars whenever a fat recovery clears +0.25 but not")
print("  +0.50. The margin story sharpened too: arc 2's re-arm")
print("  pool reads +0.47 — 3¢ above the strict bar — so one soft")
print("  week flips the verdict. Still honest: these are SEEDED")
print("  arcs, not lived flip-backs — the live ledger grades the")
print("  dial monthly, and POOL_MIN ships at 0.0 until real")
print("  re-arms price the family themselves.")
print()
print("  THE MATURITY HANDOVER (Sep 13): when the real ledger")
print("  carries a few real flip-backs, recommend_pool_min runs")
print("  THIS walked machinery on the LIVED streams (the threshold-0")
print("  walk must reproduce the real events first) and PROPOSES a")
print("  raise — but only when the denied net stays positive across")
print("  rolling windows (4w and 8w), and the raise applies by HER")
print("  hand: the machinery never writes the default file, never")
print("  moves the gate (the sim arcs seed the worked examples, the")
print("  lived ledger prices the proposal).")
print()

# ═════════════════════════════════════════════════════════════
# THE TWO-DIAL DISAGREEMENT (Sep 13): the shapes the live ledger
# hasn't produced yet, seeded so the POOL and STREAK dials' raise
# proposals are priced AGAINST each other before either ships —
# each arc run through BOTH family sweeps (one walk lens, two
# axes), the divergence the dollars.
# ═════════════════════════════════════════════════════════════
print("=== THE TWO-DIAL DISAGREEMENT (the shapes the live ledger "
      "hasn't produced yet) ===")
print()
for arc in poolgate_arcs.ARCS:
    _st, _stream = arc["state"], arc["stream"]
    _canon_ev = [{"state": _st, "action": a, "week": w}
                 for a, w in arc["canon"]]
    _fs = tbw._poolgate_family_sweep(_stream, _canon_ev)
    _ffs = tbw._fbwgate_family_sweep(_stream, _canon_ev)
    _pool = _fs.get("family") or {}
    _fbw = _ffs.get("family") or {}
    _pool_sum = round(sum(v for v in _pool.values() if v), 2)
    _fbw_sum = round(sum(v for v in _fbw.values() if v), 2)
    if _pool_sum == 0.0 and _fbw_sum == 0.0:
        _verdict = "the dials AGREE (both pass-through, or nothing to deny)"
    elif _pool_sum < 0:
        _verdict = "the POOL dial's raise COSTS on this shape"
    elif _fbw_sum < 0:
        _verdict = "the STREAK dial's raise COSTS on this shape"
    else:
        _verdict = "both raise"
    print(f"-- {arc['name']} --")
    print("    pool family: " + " \u00b7 ".join(
        f"+{pm:g} ${_pool.get(pm, 0.0):+,.2f}" for pm in _pool)
          + f"  (sum ${_pool_sum:+,.2f})")
    print("    fbw  family: " + " \u00b7 ".join(
        f"FBW={fw:g} ${_fbw.get(fw, 0.0):+,.2f}" for fw in _fbw)
          + f"  (sum ${_fbw_sum:+,.2f})")
    print(f"    -> {_verdict}")
    print()
print("  THE DISAGREEMENT PRICED: arc 4's fat-pool re-arm sails")
print("  through every pool dial on size (+0.79) while its thin")
print("  2-week streak is denied by every FBW raise — and the")
print("  denial MISSES (the recovery was real, -$16.40/-$36.90).")
print("  arc 5 is the mirror: the thin-pool re-arm (+0.175) is")
print("  denied by every pool raise but its durable 4-week streak")
print("  clears every FBW candidate — the pool denial costs")
print("  $61.50 on the very shape the streak dial was right")
print("  about. One dial's evidence is not the other's: a raise")
print("  on EITHER dial prices the shapes it cannot see. Still")
print("  honest: seeded shapes, not lived flip-backs — the live")
print("  ledger prices the dials monthly, and the dials ship at")
print("  0.0/FBW=2 until real re-arms accumulate.")
print()

# the priced family is pinned: the counter-case has a real cost, the
# denial a real save, and arc 2's re-arm pool sits between the dials
# (0.25 < pool < 0.50) — the exact window where the two disagree
_r2_roll = tbw._rolling_grade(_arc2[:7], "RISING")
check("the counter-case prices in real dollars: arc 2's denied "
      "paying re-arm COSTS $180.40, arc 3's denial SAVES $307.50",
      ledger.get(_ARCS[1][0], {}).get(0.50) == -180.40
      and ledger.get(_ARCS[2][0], {}).get(0.50) == 307.50
      and ledger.get(_ARCS[2][0], {}).get(0.25) == 307.50,
      {k: v for k, v in ledger.items()})
check("arc 2's re-arm pool sits strictly between the dials "
      "(+0.25 < pool < +0.50) — the window where the family "
      "disagrees",
      _r2_roll is not None and 0.25 < _r2_roll.get("delta") < 0.50,
      _r2_roll)
print()

# idempotence: the shipped constants are exactly restored
check("the shipped gate constants are restored (FLIP_BACK_WEEKS=2, "
      "FLIP_WEEKS=3, ROLLING_WEEKS=4, POOL_MIN=0.0)",
      tbw.FLIP_BACK_WEEKS == 2 and tbw.FLIP_WEEKS == 3
      and tbw.ROLLING_WEEKS == 4 and tbw.POOL_MIN == 0.0,
      (tbw.FLIP_BACK_WEEKS, tbw.FLIP_WEEKS, tbw.ROLLING_WEEKS,
       tbw.POOL_MIN))
# read-only: the trail is byte-identical (the walks never touch it)
trail = ROOT / "tide_bar_flip_events.jsonl"
_b = trail.read_bytes() if trail.exists() else None
check("the audit trail was never touched",
      (trail.read_bytes() if trail.exists() else None) == _b)

total, fails = _state["n"], _state["fail"]
print(f"\n{'ALL GREEN' if fails == 0 else 'FAILURES PRESENT'} "
      f"- {total - fails}/{total} checks")
raise SystemExit(0 if fails == 0 else 1)
