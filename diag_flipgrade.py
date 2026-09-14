"""THE FLIP GRADE, SIMULATION (Sep 11): does a flipped state actually
stop losing? A SEEDED replay — plausible futures built around the real
W37 record — kept for demonstration and regression reading. The REAL,
ledger-driven grade is the flip-grade driver (tidebar_weekly.grade_ledger,
monthly): it judges the actual history and writes _flipgrade.txt; this
script writes _flipgrade_sim.txt only, so a manual run can never
clobber the driver's record.

  A. THE BENCH: W37's fit edge (-0.82) followed by the losing streak
     the bar earned. Does the flip land, and what did staying live
     cost on the metric the flip keys on?
  B. THE FLIPPED WEEKS: the quantile is live, the fit's fires are
     counterfactual on the same candidate stream. Did the state stop
     losing — does the live bar out-earn the benched fit? Then the
     fit's recovery: two counterfactual winning weeks + a positive
     pool -> the flip-back. Was the re-arm earned on the weeks the
     fit did NOT trade?
  C. THE INVERSION PROOF: the pre-fix code read the raw
     in_force-other delta while flipped (quantile - fit), so the fit
     re-armed only by LOSING. The old rule is recomputed inline on
     the same records to show the difference.
  D. THE COST ARC (Sep 12): a second replay (RISING) where the bench
     decision COSTS — the fit's counterfactual out-earns the live
     quantile over the governed window and the flip-back re-arms at
     the first contract-legal pass — so the trail carries both arcs
     of the machinery beside each other.
  E. THE RE-FLIP ARC (Sep 12): a third replay (FLAT) where the
     FLIP-BACK ITSELF COSTS — the fit re-arms, bleeds again, and the
     gate re-flips it. A flip-back's governed window can cross the
     re-arm itself, where BOTH sides change seats week to week, so
     the aftermath pools each side era-aware: every week contributes
     the seat its other_src tag assigns. All four arcs of the bench
     machinery then live on the trail: bench PAID (OUT), bench COST
     (RISING), re-arm PAID (OUT's flip-back), re-arm COSTS (FLAT).

Every record is regime-tagged by other_src exactly as report_now
writes them; the real ledgers are never touched. Writes
_flipgrade_sim.txt.
Run: .venv\\Scripts\\python.exe -X utf8 diag_flipgrade.py
"""
import json
from pathlib import Path

import tidebar_weekly as tbw

ROOT = Path(__file__).parent
OUT = ROOT / "_flipgrade_sim.txt"
HIST = ROOT / "_tidebarweekly_history.jsonl"

lines = []


def say(s=""):
    lines.append(s)


def wk(tag, live, other, src="quantile", fires_i=82, fires_o=46,
       state="OUT"):
    """A week record the way report_now writes it: live = the in-force
    bar's $/fire, other = the counterfactual's."""
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


def old_rule_flip_back(recs):
    """The PRE-FIX rule recomputed: streaks and pool on the RAW
    in_force-other delta (no regime normalization)."""
    raw = []
    for rec in recs:
        st = (rec.get("states") or {}).get("OUT") or {}
        inf, oth = st.get("in_force") or {}, st.get("other") or {}
        raw.append(round(inf["usd_per_fire"] - oth["usd_per_fire"], 2)
                   if inf.get("fires") and oth.get("fires") else None)
    winning = 0
    for d in reversed(raw):
        if d is None or d <= 0:
            break
        winning += 1
    two_sided = [r for r in recs if tbw._week_fit_edge(r, "OUT") is not None][-4:]
    pool_raw = []
    for rec in two_sided:
        st = rec["states"]["OUT"]
        pool_raw.append(st["in_force"]["usd_per_fire"]
                        - st["other"]["usd_per_fire"])
    pool = sum(pool_raw) / len(pool_raw) if pool_raw else None
    return winning >= 2 and pool is not None and pool > 0, winning, pool


say("=== THE FLIP GRADE — did flipped states stop losing? (Sep 11) ===")
say("")

# ── the real seed ────────────────────────────────────────────────────
real = None
try:
    for line in HIST.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if r.get("week") == "2026-W37" and (r.get("states") or {}).get("OUT"):
                real = r
                break
except Exception:
    pass
if real is None:
    say("no real 2026-W37 OUT record on disk — seeding the textbook case")
    real = wk("2026-W37", 4.00, 4.82)
inf = real["states"]["OUT"]["in_force"]
oth = real["states"]["OUT"]["other"]
src = real["states"]["OUT"].get("other_src") or "quantile"
live_is_fit = src.startswith("quantile")
say("SEED — the real 2026-W37 OUT record:")
say(f"  in force ({'the outcome fit' if live_is_fit else 'the quantile'}): "
    f"{inf['fires']} fires ${inf['usd_per_fire']:+.2f}/fire "
    f"(total ${inf['usd']:+.2f})")
say(f"  counterfactual ({src}): {oth['fires']} fires "
    f"${oth['usd_per_fire']:+.2f}/fire (total ${oth['usd']:+.2f})")
say(f"  the fit's realized edge that week: "
    f"{tbw._week_fit_edge(real, 'OUT'):+.2f} $/fire")
say("")

# ── SCENARIO A: the bench lands ──────────────────────────────────────
say("-- A. THE BENCH — the losing streak the fit earned --")
d_i, d_o = inf["usd_per_fire"], oth["usd_per_fire"]
fi, fo = inf["fires"], oth["fires"]
recs = [real,
        wk("2026-W38", d_i - 0.4, d_o + 0.3, fires_i=fi, fires_o=fo),
        wk("2026-W39", d_i - 0.5, d_o + 0.2, fires_i=fi, fires_o=fo),
        wk("2026-W40", d_i - 0.6, d_o + 0.1, fires_i=fi, fires_o=fo)]
flips = {}
for rec in recs:
    evs, flips = tbw._evaluate_flips(recs[:recs.index(rec) + 1], flips)
    roll = tbw._rolling_grade(recs[:recs.index(rec) + 1], "OUT")
    ev = evs["OUT"]
    st = rec["states"]["OUT"]
    action = (ev["action"] + f" (fit losing {ev['losing_weeks']} weeks, "
              f"pooled fit edge {ev['roll_delta']:+.2f})") if ev else "-"
    say(f"  {rec['week']}: fit ${st['in_force']['usd_per_fire'] if live_is_fit else st['other']['usd_per_fire']:+.2f}/fire"
        f" vs quantile ${st['other']['usd_per_fire'] if live_is_fit else st['in_force']['usd_per_fire']:+.2f}/fire"
        f"  edge {tbw._week_fit_edge(rec, 'OUT'):+.2f}  pooled {roll['delta']:+.2f}  -> {action}")
say("")
fit_w = [r["states"]["OUT"]["in_force"] if live_is_fit
         else r["states"]["OUT"]["other"] for r in recs[:3]]
q_w = [r["states"]["OUT"]["other"] if live_is_fit
       else r["states"]["OUT"]["in_force"] for r in recs[:3]]
fit_usd = sum(g["usd"] for g in fit_w)
q_usd = sum(g["usd"] for g in q_w)
fit_f = sum(g["fires"] for g in fit_w)
q_f = sum(g["fires"] for g in q_w)
say("  HINDSIGHT IN DOLLARS (W37-W39, the fit still live):")
say(f"    the fit: ${fit_usd:+.2f} over {fit_f} fires "
    f"(${fit_usd / fit_f:+.2f}/fire)")
say(f"    the quantile, same weeks: ${q_usd:+.2f} over {q_f} fires "
    f"(${q_usd / q_f:+.2f}/fire)")
say(f"    per-dollar efficiency lost to the fit: "
    f"${q_usd / q_f - fit_usd / fit_f:+.2f}/fire x {fit_f} fires")
say("    NOTE the contract: the flip keys on $/fire — per-dollar")
say("    efficiency — because the size ladder scales exposure per fire.")
say("    Raw totals still favor the fit's fatter fire count here; the")
say("    bench trades total dollars for efficiency. That tension is the")
say("    known open question on this machinery (see the verdict).")
say("")

# ── SCENARIO B: flipped weeks + the recovery ─────────────────────────
say("-- B. THE FLIPPED WEEKS — does the state stop losing? --")
say("The quantile is live now; the fit's fires are counterfactual on")
say("the SAME candidate stream. First two weeks: the live quantile")
say("out-earns the benched fit (the bleed stops). Then the fit's")
say("counterfactual recovers on a THINNING stream — and the flip-back")
say("lands the week the pool turns positive.")
W41 = wk("2026-W41", d_o + 0.4, d_i - 0.7, src="outcome-fitted",
         fires_i=fo, fires_o=fi)
W42 = wk("2026-W42", d_o + 0.3, d_i - 0.8, src="outcome-fitted",
         fires_i=fo, fires_o=fi)
W43 = wk("2026-W43", d_o - 0.6, d_i + 2.08, src="outcome-fitted",
         fires_i=fo, fires_o=20)
W44 = wk("2026-W44", d_o - 0.5, d_i + 2.08, src="outcome-fitted",
         fires_i=fo, fires_o=20)
W45 = wk("2026-W45", d_o - 0.6, d_i + 2.08, src="outcome-fitted",
         fires_i=fo, fires_o=20)
W46 = wk("2026-W46", d_o - 0.7, d_i + 1.3, src="outcome-fitted",
         fires_i=fo, fires_o=fi)
recs_b = recs + [W41, W42, W43, W44, W45, W46]
flips_b = {"OUT": {"flipped": True, "since": "2026-W40", "reason": ""}}
for rec in [W41, W42, W43, W44, W45, W46]:
    idx = recs_b.index(rec) + 1
    evs, flips_b = tbw._evaluate_flips(recs_b[:idx], flips_b)
    roll = tbw._rolling_grade(recs_b[:idx], "OUT")
    st = rec["states"]["OUT"]
    live_q, cf_fit = st["in_force"], st["other"]   # flipped: quantile live
    ev = evs["OUT"]
    action = (ev["action"] + f" (fit cf winning {ev['winning_weeks']} weeks, "
              f"pooled fit edge {ev['roll_delta']:+.2f})") if ev else "-"
    say(f"  {rec['week']}: live quantile ${live_q['usd_per_fire']:+.2f}/fire, "
        f"fit cf ${cf_fit['usd_per_fire']:+.2f}/fire "
        f"(fit edge {tbw._week_fit_edge(rec, 'OUT'):+.2f}, "
        f"pooled {roll['delta']:+.2f}) -> {action}")
say("")
q41, q42 = W41["states"]["OUT"]["in_force"], W42["states"]["OUT"]["in_force"]
f41, f42 = W41["states"]["OUT"]["other"], W42["states"]["OUT"]["other"]
q_live = (q41["usd"] + q42["usd"]) / (q41["fires"] + q42["fires"])
cf = (f41["usd"] + f42["usd"]) / (f41["fires"] + f42["fires"])
say("  HINDSIGHT IN DOLLARS:")
say(f"    W41-W42 (the stopped bleed): the live quantile realized "
    f"${q41['usd'] + q42['usd']:+.2f} (${q_live:+.2f}/fire) while the")
say(f"    benched fit's counterfactual would have paid ${cf:+.2f}/fire —")
say(f"    the flip SAVED ${q_live - cf:+.2f}/fire x "
    f"{q41['fires'] + q42['fires']} fires = "
    f"${(q_live - cf) * (q41['fires'] + q42['fires']):+.2f} vs staying benched-fit.")
f43, f44 = W43["states"]["OUT"]["other"], W44["states"]["OUT"]["other"]
q43, q44 = W43["states"]["OUT"]["in_force"], W44["states"]["OUT"]["in_force"]
say(f"    W43-W44 (the recovery begins, on a thinning stream): the fit's "
    f"counterfactual out-earned ${(f43['usd_per_fire'] + f44['usd_per_fire']) / 2:+.2f}/fire "
    f"vs the live quantile's "
    f"${(q43['usd_per_fire'] + q44['usd_per_fire']) / 2:+.2f} — 2 consecutive "
    f"fit-winning weeks, BUT the pooled 4-week edge was still "
    f"negative, so the POOL GUARD held the bar benched (see W44: no "
    f"flip-back). The recovery must out-earn the bad weeks' pooled")
say("    memory, not just a lucky fortnight — the pool crossed zero at")
say("    the THIRD winning week (W45): the thin 20-fire recovery weeks")
say("    still outweighed the fat early losses, because the pool reads")
say("    $/fire, not totals. The re-arm was earned on the fit's OWN")
say("    axis, on weeks it did not trade — a thin week still counts.")
say("")

# ── SCENARIO C: the inversion proof ──────────────────────────────────
say("-- C. THE INVERSION PROOF — the pre-fix rule, recomputed --")
say("Flipped-regime weeks where the fit's counterfactual LOSES two")
say("weeks running (the fit stays dead weight). The old code read the")
say("raw in_force-other delta = quantile - fit > 0 there, i.e. it saw")
say("'winning weeks' and re-armed a losing fit.")
BAD1 = wk("2026-W41", d_o + 0.6, d_i - 0.2, src="outcome-fitted",
          fires_i=fo, fires_o=fi)
BAD2 = wk("2026-W42", d_o + 0.5, d_i - 0.3, src="outcome-fitted",
          fires_i=fo, fires_o=fi)
old_fire, old_win, old_pool = old_rule_flip_back(recs + [BAD1, BAD2])
evs_fixed, _ = tbw._evaluate_flips(recs + [BAD1, BAD2],
                                   {"OUT": {"flipped": True}})
say(f"  the fit's counterfactual edges those weeks: "
    f"{tbw._week_fit_edge(BAD1, 'OUT'):+.2f}, "
    f"{tbw._week_fit_edge(BAD2, 'OUT'):+.2f} (losing)")
say(f"  OLD rule: raw 'winning' streak {old_win}, raw pool "
    f"{old_pool:+.2f} -> flip-back fires = {old_fire}")
say(f"  SHIPPED rule: flip-back fires = "
    f"{evs_fixed['OUT'] is not None and evs_fixed['OUT']['action'] == 'flip_back'} "
    f"(stays benched: the fit never proved it)")
say("")

# ── THE WORKED EXAMPLE ON THE TRAIL (Sep 11): the replay's two
#    decisions land on the REAL audit trail marked sim: true, with
#    their simulated aftermath embedded (the flip's full governed
#    window W41..W46 pooled; the flip-back's window runs past the
#    replay's end, so it stays honestly pending). Idempotent on
#    (state, action, week, sim) — a re-run backfills nothing twice —
#    and invisible to every machinery consumer: trend glyphs, bench
#    spans, and the flip grade all read _real_flip_events(); only the
#    console's audit view renders sims, under a SIMULATION badge, so
#    the view carries a worked example before the machinery ever
#    benches a state for real.
def _pooled_seat(rcs, state, want_fit):
    """Pool ONE side of a window, ERA-AWARE: each week contributes the
    seat its other_src tag assigns — other_src naming the quantile
    means the fit is live (its seat is in_force, the quantile's is
    other), and once the fit is benched the seats swap. A window that
    crosses a re-arm (arc 3's flip-back) has BOTH sides moving seats
    week to week, so neither side can be read from one fixed seat.
    want_fit selects the outcome-fitted side, else the quantile side."""
    f, u = 0, 0.0
    for r in rcs:
        st = r["states"][state]
        fit_live = (st.get("other_src") or "quantile").startswith(
            "quantile")
        fit_seat = st["in_force"] if fit_live else st["other"]
        q_seat = st["other"] if fit_live else st["in_force"]
        seat = fit_seat if want_fit else q_seat
        f += seat["fires"]
        u += seat["usd"]
    return {"fires": f, "usd_per_fire": round(u / f, 2) if f else 0.0}


def _replay_arc(recs_r, state, note):
    """Walk ONE seeded replay through the real decision rule
    (_evaluate_flips) and embed each decision's simulated aftermath
    (the same pooled-window math the shared _event_aftermath uses).
    Returns the sim events, deduped later by (state, action, week)."""
    decisions_r = []
    fl_walk = {}
    for i, rec in enumerate(recs_r):
        evs_w, fl_walk = tbw._evaluate_flips(recs_r[:i + 1], fl_walk)
        if evs_w.get(state):
            d = dict(evs_w[state])
            d["week"] = rec["week"]
            d["ts"] = f"simulated replay ({rec['week']})"
            decisions_r.append(d)
    events_r = []
    for j, d in enumerate(decisions_r):
        nxt = decisions_r[j + 1] if j + 1 < len(decisions_r) else None
        ev_weeks = [x.get("week") for x in (d.get("evidence") or [])]
        idxs = [i for i, r in enumerate(recs_r) if r["week"] in ev_weeks]
        after = (max(idxs) + 1) if idxs else None
        end = len(recs_r)
        if nxt:
            n_weeks = [x.get("week") for x in (nxt.get("evidence") or [])]
            n_idxs = [i for i, r in enumerate(recs_r)
                      if r["week"] in n_weeks]
            if n_idxs:
                end = max(n_idxs) + 1
        win = recs_r[after:end] if after is not None else []
        flip = d.get("action") == "flip"
        if win:
            # both sides pooled ERA-AWARE: a post-flip window is one
            # regime (the quantile live, the fit benched) so the tags
            # never move — but a post-flip-back window (arc 3) crosses
            # the re-arm, where the seats swap. One pooler handles both.
            dec = _pooled_seat(win, state, want_fit=not flip)
            cf = _pooled_seat(win, state, want_fit=flip)
            if dec["fires"] and cf["fires"]:
                d["sim_aftermath"] = {
                    "status": "graded", "weeks": len(win),
                    "realized": dict(dec, bar="quantile" if flip else "fit"),
                    "counterfactual": dict(cf, bar="fit" if flip
                                           else "quantile"),
                    "verdict": ("the decision PAID"
                                if dec["usd_per_fire"] >= cf["usd_per_fire"]
                                else "the decision COST — the benched side "
                                     "would have paid better")}
        events_r.append(dict(d, state=state, sim=True, sim_source=note))
    return events_r


sim_events = _replay_arc(
    recs_b, "OUT", "diag_flipgrade.py seeded replay (the bench PAYS)")

# ── ARC 2 (Sep 12): the bench COSTS — RISING's textbook replay. The
#    fit loses W37-W39 (the flip lands), but the recovery is real:
#    the fit's counterfactual out-earns the live quantile over the
#    governed window, the flip-back re-arms at the FIRST contract-legal
#    pass (2 cf-winning weeks + a positive pool, W43), and the embedded
#    aftermath grades the bench COST. Dedupe keys are disjoint from
#    arc 1 (the state differs), so both arcs live on the trail.
recs_c = [wk("2026-W37", 3.60, 4.00, state="RISING"),
          wk("2026-W38", 3.30, 3.90, state="RISING"),
          wk("2026-W39", 3.10, 3.95, state="RISING"),
          wk("2026-W40", 3.60, 4.55, src="outcome-fitted",
             fires_i=fo, fires_o=fi, state="RISING"),
          wk("2026-W41", 4.40, 3.35, src="outcome-fitted",
             fires_i=fo, fires_o=fi, state="RISING"),
          wk("2026-W42", 3.45, 4.70, src="outcome-fitted",
             fires_i=fo, fires_o=fi, state="RISING"),
          wk("2026-W43", 3.50, 4.60, src="outcome-fitted",
             fires_i=fo, fires_o=fi, state="RISING")]
sim_events += _replay_arc(
    recs_c, "RISING",
    "diag_flipgrade.py seeded replay (the bench COSTS)")

# ── ARC 3 (Sep 12): the FLIP-BACK ITSELF COSTS — FLAT's textbook
#    replay. The fit bleeds W37-W39 (the flip lands), re-arms at W40
#    — its two winning weeks outrun the pooled memory (pool +0.16 at
#    W41) — then bleeds AGAIN: W42-W44 all lose on the now-live fit
#    and the pool sinks back to -0.60, so the gate RE-FLIPS at W44.
#    The flip-back's governed window (W42-W44) crosses no seat swap
#    itself (the fit is live throughout), but the REPLAY proves the
#    era-aware pooler: pre-re-arm W41's fit fires sit in 'other',
#    post-re-arm weeks' in 'in_force' — one tag-driven read handles
#    every window shape. Dedupe keys stay disjoint from arcs 1-2 (the
#    state differs), so all three arcs live on the trail.
recs_d = [wk("2026-W37", 3.00, 3.40, state="FLAT"),
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
sim_events += _replay_arc(
    recs_d, "FLAT",
    "diag_flipgrade.py seeded replay (the flip-back COSTS)")
n_back = tbw.persist_sim_events(sim_events)
say("")
say(f"-- THE TRAIL -- all four arcs land on the audit trail marked "
    f"SIMULATION ({n_back} appended this run; a re-run refreshes its "
    f"own records in place, so the worked examples always match this "
    f"seed). The console's audit view renders them as the worked "
    f"examples; no machinery consumer reads them.")
_a_pay = next((e for e in sim_events if e["state"] == "OUT"
               and e["action"] == "flip" and e.get("sim_aftermath")), None)
_a_cost = next((e for e in sim_events if e["state"] == "RISING"
                and e["action"] == "flip" and e.get("sim_aftermath")), None)
_a_rearm = next((e for e in sim_events if e["state"] == "FLAT"
                 and e["action"] == "flip_back" and e.get("sim_aftermath")),
                None)
if _a_pay and _a_cost and _a_rearm:
    _sp = _a_pay["sim_aftermath"]
    _sc = _a_cost["sim_aftermath"]
    _wp = ("PAID" if _sp["verdict"].startswith("the decision PAID")
           else "COST")
    _wc = ("PAID" if _sc["verdict"].startswith("the decision PAID")
           else "COST")
    say(f"  arc 1 (OUT): the bench {_wp} — the quantile realized "
        f"${_sp['realized']['usd_per_fire']:+.2f}/fire vs the fit's "
        f"${_sp['counterfactual']['usd_per_fire']:+.2f}/fire over the "
        f"{_sp['weeks']} governed weeks.")
    say(f"  arc 2 (RISING): the bench {_wc} — the quantile realized "
        f"${_sc['realized']['usd_per_fire']:+.2f}/fire vs the fit's "
        f"${_sc['counterfactual']['usd_per_fire']:+.2f}/fire over the "
        f"{_sc['weeks']} governed weeks; the flip-back re-armed at the "
        f"first contract-legal pass (two cf-winning weeks and a "
        f"positive pool), so a wrong bench ends as soon as the "
        f"pooled memory allows.")
    _sr = _a_rearm["sim_aftermath"]
    _wr = ("PAID" if _sr["verdict"].startswith("the decision PAID")
           else "COST")
    say(f"  arc 3 (FLAT): the RE-ARM {_wr} — the live quantile "
        f"realized ${_sr['realized']['usd_per_fire']:+.2f}/fire vs the "
        f"fit's ${_sr['counterfactual']['usd_per_fire']:+.2f}/fire over "
        f"the {_sr['weeks']} governed weeks; the fit re-armed fast, "
        f"bled again, and the gate RE-FLIPPED it the first legal pass "
        f"the second bleed allowed — a wrong re-arm does not linger, "
        f"and its cost is graded on the same era-aware pooled math.")

# ── THE VERDICT ──────────────────────────────────────────────────────
say("-- VERDICT --")
say("Does a flipped state stop losing? The machinery is built so it")
say("must, and the replay shows the arc: the bench requires 3")
say("consecutive fit-losing weeks AND a negative pooled fit edge; the")
say("flipped weeks are then graded live-quantile vs counterfactual-fit")
say("on the same candidate stream, so 'stopped losing' is directly")
say("observable in $/fire; and the fit returns only after consecutive")
say("counterfactual winning weeks AND a positive pooled edge — the")
say("replay shows the pool guard holding the re-arm two extra weeks")
say("(W44) until the recovery out-earned the bad weeks' pooled memory —")
say("the flip-back landed at W45, the first week the pool crossed zero.")
say("The trail now carries ALL FOUR arcs: arc 1 (OUT) where the bench")
say("PAID, arc 2 (RISING) where the bench COST, arc 3 (FLAT) where")
say("the re-arm itself COSTS — there the fit re-armed fast, bled")
say("again, and the gate re-flipped it at the first legal pass the")
say("second bleed allowed, so a wrong re-arm does not linger either.")
say("Arc 2's flip-back re-armed at the first contract-legal pass, so")
say("the wrong bench ended as soon as the contract allows.")
say("Both horizons run on the canonical fit−quantile axis, regime-")
say("normalized by each week's other_src (the pre-fix code would have")
say("re-armed a losing fit — section C shows it firing on raw deltas")
say("the shipped code correctly reads as losing; the shipped rule")
say("cannot). Two honest caveats: (1) the decision metric is $/fire —")
say("per-dollar efficiency — while raw totals can still favor the")
say("benched fit's fatter fire count, which is the ladder-vs-selection")
say("tension, not a bug; (2) one real week of history exists (W37), so")
say("every flip decision above is a seeded replay, not a lived event —")
say("from W38 on this grade is re-runnable from the live ledger.")

OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines))
