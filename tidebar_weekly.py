"""THE TIDE BAR WEEKLY (Sep 11) — the outcome bar vs the quantile it
replaced, graded on the live candidate stream. The pass runs DAILY
(the MASTER plate is never more than a day old) but the GRADE is
always the trailing 7 days — a pass and a calendar week carry the
same evidence, and the ISO week tag keeps the flip ledger on weekly
keys so restarts and daily runs cannot double-count a week.

WHY: the per-state outcome bars (tide_learn._outcome_fit_bar) ship on
in-sample EV gates. Hindsight is the other half of the proof: every
week, the candidates the ledgers actually saw — deduped and joined to
the deep 5m store exactly like the fit's own join — are replayed under
BOTH bars, per tide state:

  - THE IN-FORCE BAR (whatever the model ships: outcome-fitted or the
    quantile) and
  - THE OTHER BAR (the quantile recomputed exactly as learn() would
    from the model's deep-history payer rate, or the state's outcome
    fit when the quantile is in force),

each graded: fires (quick >= bar), payer rate (+1.5% MFE in 60 min —
the contract the bars are FIT on), $ total and $/fire in the house
convention (1055 x brain_size_mult x 1.5% per payer). Fleet-level by
design: the per-coin personal bar rides ON TOP of the fleet bar in the
live gate, so grading the fleet bar alone isolates ITS contribution —
the same level the fits themselves price.

FAIL-SAFE: no model, a broken store, or thin evidence -> the section
says so and the previous report stands. The driver never raises; it
dies with the scanner. The report PREPENDS the newest section (the
latest verdict is always at the top) and rotates at ~500 KB.

Writes _tidebarweekly.txt.
Run: .venv\\Scripts\\python.exe -X utf8 tidebar_weekly.py
"""
import json
import logging
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("tidebar-weekly")

ROOT = Path(__file__).parent
OUT_FILE = ROOT / "_tidebarweekly.txt"
JSON_FILE = ROOT / "_tidebarweekly.json"   # the machine-readable sidecar the console's MASTER plate reads
REPORT_MAX_BYTES = 500_000

# THE REAL-FILLS LEDGERS (Sep 11): the graded stream above is the
# fits' population — candidates the ledgers SAW, priced on candles.
# This layer adds what actually HAPPENED: fired popups joined to their
# realized fills (contest_trades.jsonl pnl_net), plus the popup's own
# contract grade (mfe_pct from snapshot_outcomes) at the fire's real
# spend for fires that never filled.
SNAP_OUTCOMES_FILE = ROOT / "snapshot_outcomes.jsonl"
TRADES_FILE = ROOT / "contest_trades.jsonl"
FILL_JOIN_H = 8.0         # a fill's sold_at must land within this many hours after the fire
FILL_GOAL_PCT = 1.5       # the popup contract the fired grade uses

# THE ROLLING GRADE + THE AUTO-FLIP (Sep 11, canonical axis): each pass
# appends its per-state grades to a JSONL history; the rolling block
# pools the last ROLLING_WEEKS weeks' fires/pay/dollars for BOTH bars
# (same candidate population both sides, so the pooled $/fire
# comparison is fair even when windows overlap). Every comparison runs
# on ONE canonical axis — the OUTCOME-FITTED bar minus the QUANTILE,
# normalized by each week's own other_src — so a regime change
# mid-history (a flip) can never mix the sides or invert the sign:
# while flipped the live bar IS the quantile, and reading the raw
# in_force−other delta there would demand the fit LOSE to come back.
# A state whose fit underperforms the quantile FLIP_WEEKS weeks
# RUNNING — and whose pooled fit edge is also negative — is
# AUTO-FLIPPED to the quantile via tide_bar_flips.json, which
# tide_learn honors (the outcome fit stops shipping for that state
# until the bar earns its way back: FLIP_BACK_WEEKS winning weeks on
# the fit's COUNTERFACTUAL edge and a positive pool).
HISTORY_FILE = ROOT / "_tidebarweekly_history.jsonl"
FLIP_FILE = ROOT / "tide_bar_flips.json"
FLIP_EVENTS_FILE = ROOT / "tide_bar_flip_events.jsonl"   # the audit trail:
# one append-only record per auto-flip / flip-back, with the evidence
# weeks that triggered it
ROLLING_WEEKS = 4
FLIP_WEEKS = 3
FLIP_BACK_WEEKS = 2
# THE POOL-MINIMUM DIAL (Sep 12, sweep-proven): the re-arm's pooled
# $/fire fit edge must also clear this bar — 0.0 ships the old `rd > 0`
# gate as a named constant (behavior IDENTICAL); raising it demands a
# recovery worth more before the fit returns to live duty. Sweep
# evidence lives in diag_fbw3.py's crossed sweep.
POOL_MIN = 0.0
# THE PER-STATE POOL BAR (Sep 13): a state's flip-back pool minimum can
# LEARN from its own pooled-edge distribution instead of riding the one
# global default — richer states owe a recovery worth more, thinner
# states stay at the floor. The learner (learn_pool_min) derives each
# state's bar from the median of its own weekly fit edges (the same
# axis the gate's roll_delta is drawn from), quantized to 0.25 steps
# (the pool-grade's graduated family), and writes
# tide_bar_poolmin.json only when the EVIDENCE clears the gates: at
# least 6 two-sided edge weeks (the flip decision itself needs 3 —
# the bar must rest on more history than the streak) and a spread
# (median > floor) worth rounding to a real step. Every output is
# clamped to [0.0, 1.0]: the bar can demand a recovery worth more,
# never block a positive-pool re-arm outright. Missing/thin/failed
# states fall back to POOL_MIN — with no file (today) the shipped
# gate is byte-identical to the global dial.
POOLMIN_LEARN_FILE = ROOT / "tide_bar_poolmin.json"
POOLMIN_MIN_EDGES = 6
POOLMIN_QUANTUM = 0.25
POOLMIN_DEF_FILE = ROOT / "tide_bar_poolmin_default.json"
# THE CONFIRM-TO-APPLY STAGE (Sep 13): the newest raise proposal that
# cleared the FULL stability gate rides here for her one-click
# confirmation on the MASTER plate — the machinery stages evidence,
# never the gate; only her confirmation writes the default file.
POOLMIN_STAGED_FILE = ROOT / "tide_bar_poolmin_staged.json"
# THE EVIDENCE-GATED DEFAULT (Sep 13): how many priced real flip-backs
# the lived arc-3 sweep needs before it may move the default off 0.0 —
# fewer than this and the sweep has nothing to outvote its own noise
# with. The shipped 0.0 stands until the live ledger earns the turn.
POOLMIN_DEF_EVENTS = 3
# the counterfactual walks pin worlds through this override (the same
# finally-restore discipline as the POOL_MIN/FLIP_BACK_WEEKS pins):
# a replayed global-bar world must not inherit the learned bars, or
# the old-world baseline would silently ride the new dial.
_LEARNED_POOL_MIN_OVERRIDE = None
POOLGATE_THRESHOLDS = (0.25, 0.50)   # the graduated candidates the
# monthly ledger grade prices (the sweep family, now on live data)
FBWGATE_WEEKS = (3, 4)               # the streak dial's graduated
# candidates, priced beside the pool dial from each event's own
# decision-time stamp (winning_weeks) — the sweep's other axis, on
# live data

PERIOD_S = 86400.0          # DAILY pass (Sep 11: the MASTER plate is never
                            # more than a day old) — the GRADE is still the
                            # trailing 7 days, so a pass and a calendar week
                            # carry the same evidence; the ISO week tag keeps
                            # the flip ledger on weekly keys
FIRST_DELAY_S = 600.0       # first pass ~10 min after boot (family convention)
WINDOW_DAYS = 7             # the trailing week the report grades (unchanged)

# THE FLIP GRADE DRIVER (Sep 11): the bench machinery judged on its OWN
# real ledger — a monthly pass grades the history ledger's per-state
# streaks and pooled edges, the bench proximity, and every flip event's
# AFTERMATH in realized dollars (live bar vs benched counterfactual in
# the weeks after the decision), prepending to _flipgrade.txt. Below
# FLIPGRADE_MIN_WEEKS of history it holds honestly: the seeded replays
# (diag_flipgrade.py) stay simulations, and nothing ships.
FLIPGRADE_FILE = ROOT / "_flipgrade.txt"
FLIPGRADE_MAX_BYTES = 200_000
FLIPGRADE_JSON = ROOT / "_flipgrade.json"   # the machine-readable
# sidecar the console's FLIP GRADE plate reads (the open-bench live
# verdict travels here — the txt only knows closed windows)
FLIPGRADE_PERIOD_S = 30 * 86400.0    # monthly (between ledger changes)
FLIPGRADE_FIRST_DELAY_S = 900.0      # first pass ~15 min after boot
FLIPGRADE_MIN_WEEKS = 2              # real history needed before grading
# THE ROLLING RECKONING WINDOW (Sep 13): the cluster flag reads the
# last N COMPLETED GRADES' contributions, not the trail's cumulative
# stamp set — a persistent disagreement REGIME separates from a one-off
# bad borrow, and healed grades slide out the back. The cumulative
# totals stay in the summary (the trail's whole history); the flag is
# the regime's.
RECKONING_WINDOW_FILE = ROOT / "tide_bar_reckoning_window.jsonl"
RECKONING_WINDOW_N = 4
RECKONING_MIN_GRADES = 2             # distinct grades a burst must span
# THE WAKE (Sep 11): every weekly tide-bar pass refreshes the same week's
# record and re-grades the realized fills — the monthly flip grade would
# go stale in between. report_now sets _grade_wake after its ledger
# writes and the flip-grade loop re-grades shortly after (the settle
# delay lets the pass's remaining writes land first). A wake whose
# ledger signature matches the newest grade section is a logged no-op:
# unchanged history never multiplies sections.
FLIPGRADE_WAKE_SETTLE_S = 90.0
# THE REGIME WATCH (Sep 11): the slope thresholds shared by the scanner
# (which WARNS when a state first slides toward a bench) and the console
# (which renders the watch line with its weeks-to-go forecast) — one
# math, both sides.
REGIME_WATCH_EPS = 0.05      # |slope| below this is steady, no pressure
REGIME_WATCH_CAP = 8         # the forecast's honest cap ("~8+ wk")
REGIME_WATCH_WINDOW = 3      # the slope's trailing graded weeks

_lock = threading.Lock()
_last = {"ts": None, "summary": None}
# once-per-episode memory: states whose bench pressure the scanner has
# ALREADY warned about (reset when the pressure lifts)
_regime_warned = set()


def regime_watch(trend, flips):
    """THE REGIME WATCH — the shared math (Sep 11): which states are
    trending toward a bench or a flip-back, from the edge series'
    slope over its trailing graded weeks, with a WEEKS-TO-GO forecast
    (zero-crossing weeks at the current slope, plus the gate's
    remaining fresh weeks; capped at REGIME_WATCH_CAP, "gate due now"
    when nothing remains). A LIVE state (the fit ships) with a falling
    edge is sliding toward a bench; a BENCHED state (the quantile
    ships) with a rising canonical edge is climbing toward a
    flip-back. The other two directions name no action the machinery
    can take, so they stay unnamed. Returns ({state: phrase}) — the
    console joins it; the scanner warns on first appearance.
    Pure — no I/O, never raises."""
    try:
        import math
        out = {}
        for nm in ("OUT", "FLAT", "RISING"):
            s = [v for v in ((trend or {}).get(nm) or []) if v is not None]
            if len(s) < 2:
                continue
            win = s[-REGIME_WATCH_WINDOW:]
            slope = ((win[-1] - win[0]) / (len(win) - 1)
                     if len(win) >= 2 else 0.0)
            if abs(slope) < REGIME_WATCH_EPS:
                continue
            live = not bool((flips or {}).get(nm, {}).get("flipped"))
            streak = 0
            for v in reversed(s):
                if live and v < 0:
                    streak += 1
                elif not live and v > 0:
                    streak += 1
                else:
                    break
            e = s[-1]
            need = FLIP_BACK_WEEKS if not live else FLIP_WEEKS
            gate_weeks = max(0, need - streak)
            cross = (math.ceil(abs(e) / abs(slope))
                     if (slope > 0 and e < 0) or (slope < 0 and e > 0)
                     else 0)
            weeks_to_go = min(cross + gate_weeks, REGIME_WATCH_CAP)
            eta = ("gate due now" if weeks_to_go == 0
                   else ("~" + str(weeks_to_go) + "+ wk to gate"
                         if weeks_to_go >= REGIME_WATCH_CAP
                         else "~" + str(weeks_to_go) + " wk to gate"))
            if live and slope < 0:
                out[nm] = (f"sliding toward a bench ({e:+.2f}, {streak} "
                           f"losing week{'' if streak == 1 else 's'}, {eta})")
            elif not live and slope > 0:
                out[nm] = (f"climbing toward a flip-back ({e:+.2f}, "
                           f"{streak} winning week{'' if streak == 1 else 's'}, "
                           f"{eta})")
        return out
    except Exception:
        return {}


def warn_on_bench_pressure(trend, flips):
    """THE SCANNER'S SIDE OF THE WATCH (Sep 11): run after each weekly
    pass — logs one WARN the FIRST time a state is named under bench
    pressure (sliding toward a bench), then goes quiet for that state
    until the pressure lifts: the watch stops naming it, or a benched
    state starts CLIMBING toward its flip-back (the bench episode is
    ending either way) — after which a fresh slide warns again. Returns
    the bench-pressure phrase (whether or not it warned this pass), or
    None when no state is under bench pressure. Never raises."""
    try:
        watch = regime_watch(trend, flips)
        warned = None
        for nm, phrase in watch.items():
            if "bench" in phrase:
                if nm not in _regime_warned:
                    _regime_warned.add(nm)
                    logger.warning(
                        "REGIME WATCH: %s — the outcome bar is losing to "
                        "the quantile; at 3 losing weeks the bench takes "
                        "over. Watch the plate.", f"{nm} {phrase}")
                if warned is None:
                    warned = f"{nm} {phrase}"
            else:
                # a benched state climbing toward its flip-back: the
                # bench-pressure episode is ENDING — re-arm so a later
                # re-slide warns fresh
                _regime_warned.discard(nm)
        # pressure lifted for states the watch no longer names
        for nm in list(_regime_warned):
            if nm not in watch:
                _regime_warned.discard(nm)
        return warned
    except Exception:
        return None
_grade_wake = threading.Event()
_flipgrade_last_sig = None   # the ledger signature the newest section graded


def _grade(cands, bar):
    """One bar's grade over [(quick, payer)]: fires, payers, $, $/fire."""
    if bar is None or not cands:
        return None
    import hindsight_scorer as _hs
    from tide_learn import OUTCOME_BASE_USD, OUTCOME_GOAL_PCT
    fires = [(q, y) for q, y in cands if q >= bar]
    if not fires:
        return {"bar": round(float(bar), 1), "fires": 0, "payers": 0,
                "rate": 0.0, "usd": 0.0, "usd_per_fire": 0.0}
    usd = sum(OUTCOME_BASE_USD * _hs.brain_size_mult(float(q)) *
              (OUTCOME_GOAL_PCT / 100.0 if y else 0.0) for q, y in fires)
    npay = sum(1 for _, y in fires if y)
    n = len(fires)
    return {"bar": round(float(bar), 1), "fires": n, "payers": npay,
            "rate": npay / n, "usd": round(usd, 2),
            "usd_per_fire": round(usd / n, 2)}


def _quantile_bar_as_learn(state, model):
    """The quantile bar recomputed EXACTLY as learn() computes it: the
    (1 - payer_rate) quantile of the funnel's score history, clamped
    [BAR_MIN, BAR_MAX], with payer_rate = the model's deep-history rate
    for the state. None when the model lacks the rate or the scores."""
    import tide_learn as tl
    st = (model.get("states") or {}).get(state) or {}
    rate = st.get("payer_rate")
    scores = tl._funnel_score_quantiles()
    if rate is None or scores is None:
        return None
    raw = tl._quantile(scores, max(0.0, min(1.0, 1.0 - rate)))
    return round(max(tl.BAR_MIN, min(tl.BAR_MAX, raw)), 1)


def _week_candidates():
    """[(state, quick, payer)] for the trailing WINDOW_DAYS: both
    ledgers, deduped per pair/15-min like the fit, joined to the 5m
    store on the fits' 60-min contract. (None, why) on thin evidence."""
    import numpy as np
    import tide_learn as tl
    recs = tl._outcome_records()
    if recs is None or recs.empty:
        return None, "no candidate records"
    # timezone-safe cutoff: compare against the records' own tz-aware ts
    import pandas as pd
    cutoff_ts = pd.Timestamp.now(tz="UTC") - timedelta(days=WINDOW_DAYS)
    recs = recs[recs["ts"] >= cutoff_ts]
    if recs.empty:
        return None, f"no candidates in the last {WINDOW_DAYS} days"
    recs["bucket"] = recs["ts"].dt.floor("15min")
    recs = (recs.sort_values("quick", ascending=False)
                .drop_duplicates(subset=["pair", "bucket"])
                .reset_index(drop=True))
    rows = []
    cache = {}
    for r in recs.itertuples():
        a = cache.get(r.pair)
        if a is None:
            a = cache[r.pair] = tl._pair_5m_arrs(r.pair)
        if a is None:
            continue
        ts, h, c = a
        i = int(np.searchsorted(ts, int(r.ts.timestamp())))
        if i < 0 or i + 1 + tl.OUTCOME_WINDOW_BARS > len(c):
            continue
        entry = float(c[i])
        if entry <= 0:
            continue
        mfe = float(h[i + 1: i + 1 + tl.OUTCOME_WINDOW_BARS].max()) / entry - 1.0
        rows.append((r.state, float(r.quick),
                     mfe >= tl.OUTCOME_GOAL_PCT / 100.0))
    if not rows:
        return None, "no candidates joined to the store this week"
    return rows, None


def _fired_fills_this_week():
    """REAL FILLS for the trailing WINDOW_DAYS, per tide state:
    ({state: {'fills': n, 'pnl': $, 'winners': n, 'unfilled': n,
              'unfilled_payers': n, 'usd_if_paid': $}},
     coverage_from, why).
    Fired popups come from snapshot_outcomes.jsonl (kind == 'fired',
    the engine's own mfe_pct grade); fills join by pair, the fill's
    sold_at within FILL_JOIN_H after the fire's ts (nearest after).
    Fires without a fill keep their paper grade at the popup's contract
    (mfe_pct >= FILL_GOAL_PCT) — priced at the fire's real spend, so
    unfilled payers show what the funnel saw and the size ladder
    gated. coverage_from is the earliest fire event on disk — fills
    older than that are unattributable (fire-event logging is young).
    Never raises."""
    import pandas as pd
    out = {}
    coverage_from = None
    try:
        fires, fills = [], []
        try:
            with open(SNAP_OUTCOMES_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if r.get("kind") != "fired":
                        continue
                    fires.append(r)
        except Exception as exc:
            logger.warning("REAL FILLS: fired-popup outcomes unreadable "
                           "(%s) — the week's counterfactual grades on "
                           "what parsed", exc)
        try:
            with open(TRADES_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if r.get("sold_at") and r.get("pnl_net") is not None:
                        fills.append(r)
        except Exception as exc:
            logger.warning("REAL FILLS: sold-trade records unreadable "
                           "(%s) — the week's realized P&L grades on "
                           "what parsed", exc)
        if not fires:
            return None, None, "no fired-popup records"
        cutoff = pd.Timestamp.now(tz="UTC") - timedelta(days=WINDOW_DAYS)
        for fr in fires:
            try:
                ts = pd.Timestamp(fr["ts"])
                if ts.tzinfo is None:
                    ts = ts.tz_localize("UTC")
                else:
                    ts = ts.tz_convert("UTC")
            except Exception:
                continue
            if coverage_from is None or ts < coverage_from:
                coverage_from = ts
            if ts < cutoff:
                continue
            state = ((fr.get("tide") or "").split() or [""])[0].upper()
            if state not in ("OUT", "FLAT", "RISING"):
                continue
            st = out.setdefault(state, {"fills": 0, "pnl": 0.0,
                                        "winners": 0, "unfilled": 0,
                                        "unfilled_payers": 0,
                                        "usd_if_paid": 0.0})
            pair = str(fr.get("pair"))
            oc = fr.get("outcome") or {}
            # REAL FILL: the nearest fill of the same pair sold within
            # FILL_JOIN_H after the fire (sold_at > fire ts)
            fill = None
            best_dt = None
            for tr in fills:
                if str(tr.get("pair")) != pair:
                    continue
                try:
                    sa = pd.Timestamp(tr["sold_at"])
                except Exception:
                    continue
                dt = (sa - ts).total_seconds()
                if 0 <= dt <= FILL_JOIN_H * 3600 \
                        and (best_dt is None or dt < best_dt):
                    best_dt, fill = dt, tr
            if fill is not None:
                pnl = float(fill.get("pnl_net") or 0.0)
                st["fills"] += 1
                st["pnl"] += pnl
                if pnl > 0:
                    st["winners"] += 1
            else:
                st["unfilled"] += 1
                mfe = oc.get("mfe_pct")
                if mfe is not None and float(mfe) >= FILL_GOAL_PCT:
                    st["unfilled_payers"] += 1
                    spend = float(fr.get("spend") or 0.0)
                    st["usd_if_paid"] += spend * FILL_GOAL_PCT / 100.0
        if not out:
            return None, coverage_from, \
                "no fired popups in the tide-stamped states this week"
        return out, coverage_from, None
    except Exception as exc:
        return None, None, str(exc)


def _load_history():
    """The week records (oldest first), or []."""
    recs = []
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        recs.append(json.loads(line))
                    except Exception:
                        continue
    except Exception as exc:
        logger.warning("HISTORY unreadable (%s) — grading on what "
                       "parsed", exc)
    return sorted(recs, key=lambda r: r.get("week") or "")


def _save_history(recs):
    try:
        import os
        tmp = str(HISTORY_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        os.replace(tmp, HISTORY_FILE)
    except Exception as exc:
        # a failed history write is a MISSING GRADE, not a cosmetic one
        logger.warning("HISTORY write failed (%s) — the ledger keeps its "
                       "prior records", exc)


def _load_flips():
    try:
        return json.loads(FLIP_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}   # no flips file yet is the young-ledger normal
    except Exception as exc:
        logger.warning("FLIPS state unreadable (%s) — treating as "
                       "unbenched; the gate re-evaluates from the "
                       "ledger", exc)
        return {}


def _real_flip_events():
    """The trail minus the diag's simulated worked-example events —
    the ONLY trail the machinery may read: trend glyphs, bench spans,
    and the flip grade judge LIVED decisions, never the replay."""
    return [e for e in _load_flip_events() if not e.get("sim")]


def persist_sim_events(events):
    """Append the diag's SIMULATED worked-example decisions to the real
    trail, marked sim: true with their simulated aftermath embedded —
    deduped on (state, action, week, sim): a NEW key appends, a KNOWN
    key REFRESHES its own record in place, so the worked example always
    matches its seed (the recalibrated replay data moved the dollars;
    a stale sim_aftermath would contradict the diag's fresh math on
    the trail). Real events are never touched — the sim key cannot
    collide with them. A sim event never touches the flips file, the
    trend marks, the bench spans, or the flip grade — the audit view
    is the only reader. Returns the count appended."""
    try:
        evs_saved = _load_flip_events()
        added = replaced = 0
        for ev in (events or []):
            if not ev or not ev.get("sim"):
                continue
            hit = next((e for e in evs_saved
                        if e.get("sim")
                        and e.get("state") == ev.get("state")
                        and e.get("action") == ev.get("action")
                        and e.get("week") == ev.get("week")), None)
            if hit is None:
                evs_saved.append(dict(ev))
                added += 1
            else:
                evs_saved[evs_saved.index(hit)] = dict(ev)
                replaced += 1
        if added or replaced:
            _save_flip_events(evs_saved)
        return added
    except Exception:
        return 0


def _attach_bench_final(events, recs):
    """THE BENCH'S FINAL VERDICT (Sep 12): a flip-back event carries a
    `bench_final` snapshot of the bench's OPEN live verdict at the
    moment the bench ENDED — the shared _open_bench_entries math run
    against the ledger as the gate read it, so the audit trail shows
    not just WHY the bench ended but WHAT IT HAD PAID when it did.
    Callers must invoke this BEFORE _save_flips clears the benched
    state (the helper reads the trail for the state's newest real flip
    and grades it on an open window); a state with no flip on the
    trail, or a pending window (no graded post-decision weeks), ships
    nothing — never a fabricated verdict. Pure — no I/O beyond the
    passed-in records. Never raises."""
    try:
        for st, ev in (events or {}).items():
            if not ev or ev.get("action") != "flip_back":
                continue
            st_evs = [e for e in _real_flip_events()
                      if str(e.get("state") or "?") == st
                      and not e.get("sim")]
            newest_flip = next((e for e in st_evs
                                if str(e.get("action") or "") == "flip"),
                               None)
            if newest_flip is None or not recs:
                continue
            live = _event_aftermath(
                recs, [r.get("week") or "?" for r in recs],
                newest_flip, st, next_ev=None)
            if live and live.get("status") == "graded":
                rl, cf = live["realized"], live["counterfactual"]
                paying = live["verdict"].startswith("the decision PAID")
                to_date = round(rl["usd_total"] - cf["usd_total"], 2)
                ev["bench_final"] = {
                    "weeks": live["weeks"],
                    "to_date": to_date,
                    "verdict": ("PAYING" if paying else "COSTING"),
                    "verdict_col": ("#4be38a" if paying else "#ff6b5e"),
                    "text": (
                        f"over the {live['weeks']} weeks of the bench, the "
                        f"quantile realized ${rl['usd_per_fire']:+.2f}/fire "
                        f"({rl['fires']} fires) vs the fit's "
                        f"${cf['usd_per_fire']:+.2f}/fire over the same "
                        f"weeks — to date the bench is "
                        f"{'up' if to_date >= 0 else 'down'} "
                        f"${abs(to_date):,.2f} cumulative"),
                }
    except Exception as exc:
        # the snapshot is advisory trail color; the event still persists
        logger.warning("BENCH FINAL: snapshot failed for %s (%s)",
                       list((events or {}).keys()) or "?", exc)


def borrow_reckoning(ev, st, am):
    """THE BORROW'S RECKONING (Sep 12): a real flip-back's verdict was
    BORROWED while its aftermath was still pending — and every
    flip-back's is, at closure (its governed window starts the week
    after the closure week), the classify borrow stamping it COSTING
    from the saved flips: a state whose flip_back event exists on the
    trail is, by the gate's own hand, un-benched (the PAYING borrow
    belongs to open flips, not closures). Once the aftermath lands,
    the grader asks: did the borrowed COSTING tag survive the realized
    dollars? Returns a dict (pure, no I/O, never raises):
      {status: 'agree'|'disagree'|'ungradeable', borrowed: ...}
    'disagree' carries a dollars clause; 'ungradeable' is either the
    still-pending aftermath or a not-operative event (a sim or wrong
    action never grades the replay)."""
    try:
        if str(ev.get("action") or "") != "flip_back" or ev.get("sim"):
            return {"status": "ungradeable",
                    "reason": "not-operative", "borrowed": False}
        if not am or am.get("status") != "graded":
            return {"status": "ungradeable",
                    "reason": "aftermath-pending", "borrowed": True}
        borrowed = "COSTING"
        graded = ("PAYING" if str(am.get("verdict", ""))
                  .startswith("the decision PAID") else "COSTING")
        if borrowed == graded:
            return {"status": "agree", "borrowed": borrowed,
                    "graded": graded}
        rl, cf = am["realized"], am["counterfactual"]
        gap = round(rl["usd_per_fire"] - cf["usd_per_fire"], 2)
        return {"status": "disagree", "borrowed": borrowed,
                "graded": graded, "gap_per_fire": gap,
                "realized": rl, "counterfactual": cf,
                "text": (f"the borrow said {borrowed} but the realized "
                         f"verdict is {graded}: the {rl['bar']} did "
                         f"${rl['usd_per_fire']:+.2f}/fire vs the "
                         f"{cf['bar']}'s ${cf['usd_per_fire']:+.2f}/fire "
                         f"over the {am['weeks']} governed weeks — "
                         f"${gap:+.2f}/fire the borrow read backwards")}
    except Exception:
        return {"status": "ungradeable", "reason": "error",
                "borrowed": False}


def _attach_borrow_reckoning(recs):
    """THE PASS'S RECKONING HOOK (Sep 12): after each weekly pass, any
    real flip-back whose aftermath has NOW graded gets its borrow
    stamped once — `borrow_reckoning` rides the persisted event record
    (the trail's own audit of whether the live-borrow rule told the
    truth). Idempotent by construction: already-stamped events are
    skipped, the closure-pass stamp survives _persist's dedupe, and
    only freshly-graded events re-persist. Each grading logs one
    WARNING line — an honest agree and a disagreement both land in
    scanner.log; a disagreement is never quiet. Never raises; returns
    the list of stamped states."""
    stamped = []
    try:
        if not recs:
            return stamped
        evs = _real_flip_events()
        if not evs:
            return stamped
        weeks_list = [r.get("week") or "?" for r in recs]
        real_idx = {}
        for i, e in enumerate(evs):
            real_idx.setdefault(str(e.get("state") or "?"), []).append(i)
        dirty = False
        for st in real_idx:
            sibs = real_idx[st]
            for pos, i in enumerate(sibs):
                ev = evs[i]
                if (str(ev.get("action") or "") != "flip_back"
                        or isinstance(ev.get("borrow_reckoning"), dict)):
                    continue
                nxt = (evs[sibs[pos + 1]] if pos + 1 < len(sibs)
                       else None)
                am = _event_aftermath(recs, weeks_list, ev, st,
                                      next_ev=nxt)
                if am.get("status") != "graded":
                    continue
                rk = borrow_reckoning(ev, st, am)
                if rk.get("status") == "agree":
                    _msg, _args = (
                        "BORROW RECKONING: %s's %s verdict was borrowed; "
                        "the realized aftermath AGREES — the borrow told "
                        "the truth.", (st, rk.get("borrowed")))
                elif rk.get("status") == "disagree":
                    _msg, _args = (
                        "BORROW RECKONING: %s DISAGREES — %s",
                        (st, rk.get("text")))
                else:
                    continue
                # the stamp lands BEFORE the warning: the trail is the
                # audit of record, the log line is advisory — a logging
                # misconfiguration must never erase the reckoning
                ev["borrow_reckoning"] = rk
                stamped.append(st)
                dirty = True
                try:
                    logger.warning(_msg, *_args)
                except (TypeError, ValueError, OSError):
                    # the stamp already landed on the trail — the log
                    # line is advisory by design (comment above); a
                    # rarer failure escapes to the outer handler, which
                    # names it
                    pass  # advisory log failed; the audit is safe
        if dirty:
            _save_flip_events(evs)
    except Exception as exc:
        # the reckoning stamps already live in memory; persistence is
        # retried next pass — but the miss must be visible
        logger.warning("BORROW RECKONING: trail persist failed (%s) — "
                       "stamps ride memory until the next pass", exc)
    return stamped


def _persist_flip_events(events, week_tag, ts):
    """THE AUDIT TRAIL (Sep 11): persist auto-flip / flip-back events
    with their evidence weeks, append-only and deduped on
    (state, action, week) so daily passes and restarts cannot
    double-count a decision. Fail-safe: a broken ledger never blocks
    the report."""
    try:
        evs_saved = _load_flip_events()
        added = 0
        for st_name, ev in (events or {}).items():
            if not ev:
                continue
            rec = dict(ev, state=st_name, week=week_tag, ts=ts)
            if not any(e.get("state") == st_name
                       and e.get("action") == ev["action"]
                       and e.get("week") == week_tag
                       for e in evs_saved):
                evs_saved.append(rec)
                added += 1
        if added:
            _save_flip_events(evs_saved)
        return added
    except Exception:
        return 0


def _load_flip_events():
    """The audit trail: every auto-flip / flip-back event ever fired —
    INCLUDING the diag's simulated worked-example events (sim: true).
    Machinery consumers want _real_flip_events(); only the console's
    audit view renders the trail raw. Fail-open ([] when absent) so a
    young ledger is not an error."""
    out = []
    try:
        with open(FLIP_EVENTS_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        continue   # a bad trail line is skipped, named by
                        # health_check's integrity pass, never fatal here
    except FileNotFoundError:
        return []   # no trail yet is the young-ledger normal
    except Exception as exc:
        logger.warning("FLIP TRAIL unreadable (%s) — rendering an empty "
                       "trail; health_check names the rot", exc)
    return out


def _save_flip_events(evs):
    try:
        import os, time
        tmp = str(FLIP_EVENTS_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for e in evs:
                f.write(json.dumps(e) + "\n")
        # THE FLAKE'S LESSON (Sep 12): a single os.replace can fail on
        # a transient Windows sharing violation (an indexer/AV touching
        # the fresh file) — and the fail-open below would SILENTLY drop
        # a reckoning stamp the memory path already reported as saved
        # (observed once as two suite pins failing, then passing on
        # re-run). Bounded retry with backoff: the trail is the audit
        # of record; its writes must survive ordinary file contention.
        for attempt in range(3):
            try:
                os.replace(tmp, FLIP_EVENTS_FILE)
                return
            except OSError:
                if attempt == 2:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except Exception as exc:
        # THE AUDIT-OF-RECORD RULE: a trail write that failed after its
        # bounded retry is exactly the silent-drop the flake lesson
        # bans — name it loud
        logger.warning("FLIP TRAIL write failed after retry (%s) — a "
                       "stamp the memory path reported may be absent "
                       "from disk", exc)


def _save_flips(flips):
    try:
        import os
        tmp = str(FLIP_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(flips, f, indent=1)
        os.replace(tmp, FLIP_FILE)
    except Exception as exc:
        # a lost flips state makes the gate re-evaluate from scratch —
        # the missed-bench risk must be visible
        logger.warning("FLIPS state write failed (%s) — the gate may "
                       "re-decide from the ledger next pass", exc)


def _week_fit_edge(rec, state):
    """One week record's FIT edge on the CANONICAL axis: the
    outcome-fitted bar's $/fire minus the quantile's, so > 0 always
    means 'the fit paid better that week' regardless of which side was
    live. Reads the record's own other_src (present in every history
    record since the ledger began), so a regime change mid-history —
    a flip putting the quantile in force — normalizes instead of
    inverting the sign. None when either side has no fires."""
    st = (rec.get("states") or {}).get(state)
    if not st:
        return None
    inf = st.get("in_force") or {}
    oth = st.get("other") or {}
    if not (inf.get("fires") and oth.get("fires")):
        return None
    src = (st.get("other_src") or "quantile").lower()
    fit, quant = (oth, inf) if src.startswith("outcome") else (inf, oth)
    return round(fit["usd_per_fire"] - quant["usd_per_fire"], 2)


def _rolling_grade(recs, state):
    """The pooled grade over the last ROLLING_WEEKS week records for
    ONE state, on the CANONICAL axis: the outcome-fitted bar vs the
    quantile, pooled over the same candidate weeks (apples-to-apples
    even when windows overlap). delta = fit $/fire − quantile $/fire
    (the fit edge; > 0 the fit pays better). Sides are normalized per
    week by other_src, so pooled weeks from different regimes compare
    the same two bars. None when no week graded the state."""
    f_f = f_q = p_f = p_q = 0
    u_f = u_q = 0.0
    weeks = 0
    for rec in recs[-ROLLING_WEEKS:]:
        st = (rec.get("states") or {}).get(state)
        if not st:
            continue
        inf = st.get("in_force") or {}
        oth = st.get("other") or {}
        if not (inf.get("fires") and oth.get("fires")):
            continue
        weeks += 1
        src = (st.get("other_src") or "quantile").lower()
        fit, quant = (oth, inf) if src.startswith("outcome") else (inf, oth)
        f_f += fit["fires"]
        p_f += fit.get("payers", 0)
        u_f += fit.get("usd", 0.0)
        f_q += quant["fires"]
        p_q += quant.get("payers", 0)
        u_q += quant.get("usd", 0.0)
    if not weeks:
        return None
    return {"weeks": weeks,
            "fit": {"fires": f_f, "payers": p_f, "usd": round(u_f, 2),
                    "usd_per_fire": round(u_f / f_f, 2) if f_f else 0.0},
            "quantile": {"fires": f_q, "payers": p_q, "usd": round(u_q, 2),
                         "usd_per_fire": round(u_q / f_q, 2) if f_q else 0.0},
            "delta": round((u_f / f_f if f_f else 0.0)
                           - (u_q / f_q if f_q else 0.0), 2)}


def _evaluate_flips(recs, flips_now):
    """The flip decision per state from the weekly history, on the
    CANONICAL axis (fit edge = outcome-fitted $/fire − quantile
    $/fire, normalized per week by other_src). A flip to the quantile
    needs FLIP_WEEKS consecutive weeks where the fit's edge is NEGATIVE
    and a negative pooled edge; the flip back needs FLIP_BACK_WEEKS
    consecutive weeks where the fit's edge is POSITIVE, the pooled
    $/fire delta is strictly positive, and it clears POOL_MIN $/fire
    above the quantile (0.0 ships the tested sweep's dial as a
    constant: behavior identical to the old `rd > 0` gate; raising it
    demands the recovery be worth MORE before re-arming). Returns
    ({state: event-or-None}, flips_now-mutated-copy)."""
    flips = {k: dict(v) for k, v in (flips_now or {}).items()}
    events = {}
    weeks_list = [r.get("week") or "" for r in recs]
    for state in ("OUT", "FLAT", "RISING"):
        wk = [_week_fit_edge(rec, state) for rec in recs]
        flipped = bool((flips.get(state) or {}).get("flipped"))
        losing = 0
        for d in reversed(wk):
            if d is None or d >= 0:
                break
            losing += 1
        winning = 0
        for d in reversed(wk):
            if d is None or d <= 0:
                break
            winning += 1
        roll = _rolling_grade(recs, state)
        rd = roll.get("delta") if roll else None
        # THE PER-STATE POOL BAR (Sep 13): the pool minimum this
        # state's re-arm must clear — its learned bar when the learner
        # shipped one, else the global POOL_MIN (identical today). The
        # decision-time bar rides the event record so the monthly
        # pool-grade prices the bar that actually decided, not a
        # retro-fitted one.
        _pm = _pool_min_for(state)
        ev = None
        if not flipped and losing >= FLIP_WEEKS \
                and rd is not None and rd < 0:
            ev = {"action": "flip", "losing_weeks": losing,
                  "roll_delta": rd,
                  "evidence": [{"week": weeks_list[i], "fit_edge": wk[i]}
                               for i in range(len(wk) - losing, len(wk))
                               if wk[i] is not None]}
            flips[state] = {"flipped": True, "since": None, "reason": ""}
        elif flipped and winning >= FLIP_BACK_WEEKS \
                and rd is not None and rd > 0 \
                and rd >= _pm:
            ev = {"action": "flip_back", "winning_weeks": winning,
                  "roll_delta": rd, "pool_bar": _pm,
                  "evidence": [{"week": weeks_list[i], "fit_edge": wk[i]}
                               for i in range(len(wk) - winning, len(wk))
                               if wk[i] is not None]}
            flips[state] = {"flipped": False, "since": None, "reason": ""}
        events[state] = ev
    return events, flips


def learn_pool_min(recs, min_edges: int = POOLMIN_MIN_EDGES,
                   quantum: float = POOLMIN_QUANTUM) -> dict:
    """THE PER-STATE POOL BAR (Sep 13): each state's learned POOL_MIN
    from ITS OWN pooled-edge distribution — the median of its weekly
    fit edges on the CANONICAL axis (the same axis _evaluate_flips'
    rd is drawn from, regime-normalized by other_src), quantized UP
    to the pool-grade's 0.25 steps so the learned bar is always a
    member of the family the monthly grade prices (the dial sweep and
    the live bar read the same ladder). States stay honest: fewer
    than min_edges two-sided weeks, or a median that rounds to the
    floor, leave the state on the global POOL_MIN. Every learned bar
    is clamped to [0.0, 1.0] — a positive-pool re-arm is never blocked
    outright, only asked to be worth more. Pure over its inputs; the
    caller decides persistence."""
    import math
    out = {}
    for state in ("OUT", "FLAT", "RISING"):
        edges = [e for e in (_week_fit_edge(r, state) for r in recs)
                 if e is not None]
        if len(edges) < min_edges:
            out[state] = {"pool_min": None, "n_edges": len(edges),
                          "median": None,
                          "reason": f"thin ({len(edges)}/{min_edges} "
                                    "edge weeks)"}
            continue
        edges.sort()
        n = len(edges)
        med = edges[n // 2] if n % 2 else (edges[n // 2 - 1]
                                           + edges[n // 2]) / 2.0
        bar = math.ceil(med / quantum) * quantum if med > 0 else 0.0
        bar = max(0.0, min(1.0, round(bar, 2)))
        if bar <= 0.0:
            out[state] = {"pool_min": None, "n_edges": n,
                          "median": round(med, 4),
                          "reason": "median rounds to the floor"}
        else:
            out[state] = {"pool_min": bar, "n_edges": n,
                          "median": round(med, 4), "reason": ""}
    return out


def _load_pool_min_map() -> dict:
    """The learned per-state pool bars, or {} when the learner has
    never shipped (the file's absence is today's steady state — the
    gate then rides the global POOL_MIN unchanged). Corrupt entries
    are dropped, not trusted. An active override (the counterfactual
    walks' pinned worlds) replaces the file entirely."""
    if _LEARNED_POOL_MIN_OVERRIDE is not None:
        return dict(_LEARNED_POOL_MIN_OVERRIDE)
    try:
        raw = json.loads(POOLMIN_LEARN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    # the pass nests the per-state map under "states" (with its ts and
    # n_weeks provenance); a flat {state: {...}} file is also honored
    body = (raw.get("states")
            if isinstance(raw, dict) and isinstance(raw.get("states"), dict)
            else raw)
    out = {}
    for st in ("OUT", "FLAT", "RISING"):
        v = body.get(st) if isinstance(body, dict) else None
        if (isinstance(v, dict)
                and isinstance(v.get("pool_min"), (int, float))
                and 0.0 <= float(v["pool_min"]) <= 1.0):
            out[st] = float(v["pool_min"])
    return out


def _set_learned_pool_min(mapping: dict | None) -> None:
    """The counterfactual walks' pin for the learned bars (the same
    discipline as the POOL_MIN/FLIP_BACK_WEEKS pins): a dict replaces
    the file-backed map for the walk's lifetime, None restores it.
    The FBW=3 replay's old world and the pool dial's pin worlds run
    through this so a replay's global pin is the only bar in force."""
    global _LEARNED_POOL_MIN_OVERRIDE
    _LEARNED_POOL_MIN_OVERRIDE = (dict(mapping)
                                  if mapping is not None else None)


def _evidence_pool_min() -> float | None:
    """THE EVIDENCE-GATED DEFAULT (Sep 13): the default POOL_MIN the
    lived arc-3 sweep adopted (tide_bar_poolmin_default.json), or None
    while the shipped 0.0 stands. Read-only at gate time: the file is
    written only by the recommendation pass, and every turn is a loud
    WARN in the scanner log — the dial never moves silently. A pinned
    replay world (the counterfactual walks' override) suppresses the
    default entirely: a replay's baseline is the SHIPPED global bar."""
    if _LEARNED_POOL_MIN_OVERRIDE is not None:
        return None
    try:
        raw = json.loads(POOLMIN_DEF_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    v = raw.get("pool_min") if isinstance(raw, dict) else None
    if isinstance(v, (int, float)) and 0.0 <= float(v) <= 1.0:
        return float(v)
    return None


def _pool_min_for(state: str) -> float:
    """The pool bar ONE state's flip-back gate demands — the
    resolution order: the state's learned bar (its own edge
    distribution), then the lived-evidence default (the arc-3 sweep's
    recommendation once real re-arms priced it), then the shipped
    global POOL_MIN."""
    learned = _load_pool_min_map().get(state)
    if learned is not None:
        return learned
    ev_def = _evidence_pool_min()
    return POOL_MIN if ev_def is None else ev_def


def _trend_marks(recs):
    """THE REGIME MARKS (Sep 11): flip / flip-back events projected
    onto the trend series — {state: ['action@week' | None, ...]} one
    element per history week, so the MASTER sparkline can stamp each
    marked week with a distinct glyph. None = an unmarked week. REAL
    events only — the diag's simulated worked-example events never
    stamp a lived week."""
    trail = _real_flip_events()
    out = {}
    for st in ("OUT", "FLAT", "RISING"):
        row, n_flips, n_backs = [], 0, 0
        for r in recs:
            ev = next((e for e in trail
                       if e.get("state") == st
                       and e.get("week") == r.get("week")), None)
            if ev:
                act = str(ev.get("action") or "")
                # the state's 2nd+ flip marks 'flip2' and its 2nd+
                # flip-back 'flip_back2' — regime churn (flip, re-arm,
                # re-flip) reads as one pattern on the sparkline
                if act == "flip":
                    n_flips += 1
                    sfx = "2" if n_flips > 1 else ""
                elif act == "flip_back":
                    n_backs += 1
                    sfx = "2" if n_backs > 1 else ""
                else:
                    sfx = ""
                row.append(f"{act}{sfx}@{ev.get('week')}")
            else:
                row.append(None)
        out[st] = row
    return out


def report_now():
    """One pass: grade both bars per state on the week's stream, write
    the section (newest first), stamp _last. Never raises."""
    import tide_learn as tl
    try:
        model = tl.learn()          # TTL-cached; refreshes when stale
        if model.get("err") or not model.get("states"):
            raise RuntimeError(model.get("err") or "no tide model")
        rows, why = _week_candidates()
        if rows is None:
            raise RuntimeError(why)
        by_state = {}
        for st_name, q, y in rows:
            by_state.setdefault(st_name, []).append((q, y))
        scores = tl._funnel_score_quantiles()
        week_tag = time.strftime("%G-W%V", time.gmtime())   # ISO week
        flips_now = _load_flips()
        summary = {"generated_ts": time.strftime("%Y-%m-%d %H:%M UTC",
                                                 time.gmtime()),
                   "week": week_tag, "cadence": "daily (7-day grade window)",
                   "states": {}, "flips": {}}
        lines = ["=== THE TIDE BAR WEEKLY — outcome bar vs the quantile ===",
                 f"graded at: {summary['generated_ts']}  "
                 f"({week_tag}, trailing {WINDOW_DAYS} days; "
                 f"the pass runs daily, the grade stays weekly)",
                 "fleet-level replay on the fits' 60-min contract "
                 "(+1.5% MFE); $ = 1055 x size mult x 1.5% per payer",
                 ""]
        lines.append("── REAL FILLS: the fires that actually traded ──")
        fills_by_state, fills_since, fills_why = _fired_fills_this_week()
        if fills_by_state is None:
            lines.append(f"  no real fills graded ({fills_why})")
        else:
            if fills_since is not None:
                lines.append(f"  (fire-event coverage begins "
                             f"{fills_since.strftime('%b %d %H:%M UTC')} — "
                             f"earlier fills are unattributable; the join "
                             f"thickens as the ledger ages)")
            tot_fills = sum(v["fills"] for v in fills_by_state.values())
            tot_pnl = sum(v["pnl"] for v in fills_by_state.values())
            for st_name in ("OUT", "FLAT", "RISING"):
                v = fills_by_state.get(st_name)
                if not v:
                    continue
                lines.append(
                    f"  {st_name}: {v['fills']} fills, {v['winners']} winners, "
                    f"realized ${v['pnl']:+.2f}"
                    + (f"   (+{v['unfilled']} unfilled fires, "
                       f"{v['unfilled_payers']} paid on paper, "
                       f"${v['usd_if_paid']:.2f} the size ladder gated)"
                       if v["unfilled"] else ""))
            lines.append(f"  TOTAL: {tot_fills} fills, realized ${tot_pnl:+.2f} "
                         f"— realized dollars, not the candle counterfactual")
        lines.append("")
        summary["fills"] = {"states": fills_by_state,
                            "coverage_from": (fills_since.isoformat()
                                              if fills_since is not None else None)}
        if fills_by_state is None:
            summary["fills"] = fills_why
        arr_cache = {}
        for st_name in ("OUT", "FLAT", "RISING"):
            cands = by_state.get(st_name) or []
            st_model = (model.get("states") or {}).get(st_name) or {}
            bar_live = st_model.get("bar")
            src_live = st_model.get("bar_src") or "quantile"
            if bar_live is None or not cands:
                lines.append(f"{st_name}: no grade ({'no in-force bar' if bar_live is None else 'no candidates this week'})")
                lines.append("")
                summary["states"][st_name] = None
                continue
            if src_live == "outcomes":
                bar_other = _quantile_bar_as_learn(st_name, model)
                other_src = "quantile"
                if bar_other is None:
                    # the tide-specific quantile could not be computed
                    # (thin rate or score history this build) — fall to
                    # the ALL-tide quantile, still a real counterfactual:
                    # 'what if this state had stayed on the old fixed
                    # quantile'. Only a scores-less world stays one-sided.
                    scores_all = tl._funnel_score_quantiles()
                    if scores_all:
                        raw = tl._quantile(scores_all, 0.90)
                        bar_other = round(max(tl.BAR_MIN,
                                              min(tl.BAR_MAX, raw)), 1)
                        other_src = "quantile (all-tides)"
            else:
                fit = (tl._outcome_fit_bar(st_name, scores,
                                           arr_cache=arr_cache)
                       if scores is not None else None)
                bar_other = fit["bar"] if fit else None
                other_src = "outcome-fitted"
            g_live = _grade(cands, bar_live)
            g_other = _grade(cands, bar_other) if bar_other is not None else None
            summary["states"][st_name] = {
                "n": len(cands), "in_force": g_live,
                "other": g_other, "other_src": other_src}
            lines.append(f"{st_name}  ({len(cands)} candidates this week; "
                         f"in force: {src_live} bar {bar_live:.0f})")
            lines.append(f"  IN FORCE  bar {g_live['bar']:.0f}: "
                         f"{g_live['fires']} fires, {g_live['payers']} payers "
                         f"({g_live['rate'] * 100:.1f}%), "
                         f"${g_live['usd']:.2f} (${g_live['usd_per_fire']:+.2f}/fire)")
            if g_other is None:
                lines.append(f"  {other_src:<9} bar —: not available "
                             f"(thin fit; the comparison is one-sided)")
            else:
                d = g_live["usd_per_fire"] - g_other["usd_per_fire"]
                lines.append(f"  {other_src:<9} bar {g_other['bar']:.0f}: "
                             f"{g_other['fires']} fires, {g_other['payers']} payers "
                             f"({g_other['rate'] * 100:.1f}%), "
                             f"${g_other['usd']:.2f} (${g_other['usd_per_fire']:+.2f}/fire)"
                             f"   [in-force Δ$/fire {d:+.2f}]")
            lines.append("")
        # ── THE ROLLING GRADE + THE AUTO-FLIP (Sep 11) ──────────────
        recs = _load_history()
        recs = [r for r in recs if r.get("week") != week_tag]
        # the week's REALIZED fills ride the record (compact: fills +
        # pnl only) so the fills trend can be replayed per week; a
        # pass with no gradable fills stores None (a gap, not a zero)
        _rec_fills = None
        if isinstance(fills_by_state, dict):
            _rec_fills = {nm: {"fills": int(v.get("fills", 0)),
                               "pnl": float(v.get("pnl", 0.0))}
                          for nm, v in fills_by_state.items()}
        recs.append({"week": week_tag,
                     "generated_ts": summary["generated_ts"],
                     "states": summary["states"],
                     "fills": _rec_fills})
        _save_history(recs)
        events, flips_new = _evaluate_flips(recs, flips_now)
        # THE BENCH'S FINAL VERDICT (Sep 12): a flip-back snapshots the
        # bench's open live verdict (to-date dollars) at the moment the
        # bench ended — BEFORE _save_flips clears the benched state, so
        # the verdict is read exactly as the gate saw it.
        _attach_bench_final(events, recs)
        # THE BORROW'S RECKONING (Sep 12): any real flip-back whose
        # aftermath has NOW graded gets its live-borrow verdict stamped
        # once — agree or disagree, both WARN into scanner.log — so the
        # borrow rule is audited by the dollars it predicted over.
        _attach_borrow_reckoning(recs)
        flips_changed = flips_new != flips_now
        if flips_changed:
            for st in flips_new:
                if "since" in flips_new[st] and not flips_new[st]["since"]:
                    flips_new[st]["since"] = summary["generated_ts"]
            _save_flips(flips_new)
        summary["flips"] = flips_new
        # THE AUDIT TRAIL: append-only, deduped on (state, action, week)
        _persist_flip_events(events, week_tag, summary["generated_ts"])
        # THE TREND (Sep 11): the rolling pooled fit-vs-quantile edge
        # after EACH history week (oldest first), regime-normalized by
        # the same _rolling_grade the report block uses — the MASTER
        # plate's sparkline reads this so the outcome-vs-quantile trend
        # reads at a glance. None = a one-sided week (a gap, not a zero).
        trend = {}
        for st_name in ("OUT", "FLAT", "RISING"):
            series = []
            for i in range(len(recs)):
                r = _rolling_grade(recs[:i + 1], st_name)
                series.append(None if r is None else r.get("delta"))
            trend[st_name] = series
        summary["trend"] = trend
        # THE FILLS TREND (Sep 11): each week's REALIZED fill P&L per
        # state, from the same history records — so the plate can
        # sparkline the candle-counterfactual edge and the real dollars
        # side by side. None = the week kept no fills record (a gap).
        fills_trend = {}
        for st_name in ("OUT", "FLAT", "RISING"):
            series = []
            for r in recs:
                fr = (r.get("fills") or {}).get(st_name) \
                    if isinstance(r.get("fills"), dict) else None
                series.append(None if fr is None
                              else float(fr.get("pnl", 0.0)))
            fills_trend[st_name] = series
        summary["fills_trend"] = fills_trend
        # the regime marks ride the same series indexes — the plate's
        # sparkline stamps flip / flip-back weeks with distinct glyphs
        summary["flip_marks"] = _trend_marks(recs)
        # THE PER-STATE POOL BAR (Sep 13): the learner derives each
        # state's bar from its own pooled-edge distribution and ships
        # it only when the evidence clears the gates (6+ two-sided
        # edge weeks, a median rounding to a real 0.25 step) — thin
        # states stay on the global POOL_MIN, so today's gate is
        # byte-identical until a state's history earns its own bar.
        _learned = learn_pool_min(recs)
        _ship = {s: v for s, v in _learned.items()
                 if v.get("pool_min") is not None}
        _ship_floats = {s: float(v["pool_min"])
                        for s, v in _ship.items()}
        try:
            prev_map = _load_pool_min_map()
            if _ship_floats != prev_map:
                POOLMIN_LEARN_FILE.write_text(
                    json.dumps({"learned_ts": summary["generated_ts"],
                                "n_weeks": len(recs),
                                "states": _learned}, indent=2),
                    encoding="utf-8")
                if _ship:
                    logger.info(
                        "TIDE BAR: learned pool bars shipped %s",
                        _ship_floats)
        except Exception as exc:
            logger.info("TIDE BAR: pool-min learner persist failed (%s)",
                        exc)
        summary["poolmin_learned"] = _learned
        # THE SCANNER'S WARN (Sep 11): the first pass that names a state
        # sliding toward a bench logs a WARN — the log file carries the
        # warning too, not just the plate. Once per pressure episode.
        # THE SIDECAR TRAVELS THE WARN (Sep 12): the return rides the
        # JSON sidecar (bench_warn: {state, phrase} or None), so the
        # MASTER plate shows the warning the pass itself raised — log
        # and plate can never disagree about what was warned.
        _warn_phrase = warn_on_bench_pressure(trend, flips_new)
        summary["bench_warn"] = (
            {"state": _warn_phrase.split(" ", 1)[0],
             "phrase": _warn_phrase}
            if isinstance(_warn_phrase, str) else None)
        roll_lines = []
        for st_name in ("OUT", "FLAT", "RISING"):
            roll = _rolling_grade(recs, st_name)
            if roll is None:
                continue
            fit, quant = roll["fit"], roll["quantile"]
            msg = (f"  {st_name} pooled {roll['weeks']}w: "
                   f"FIT {fit['fires']} fires ${fit['usd_per_fire']:+.2f}/fire"
                   f"  vs  QUANTILE {quant['fires']} fires "
                   f"${quant['usd_per_fire']:+.2f}/fire"
                   f"   fit edge {roll['delta']:+.2f}")
            _fl = (flips_new.get(st_name) or {}).get("flipped")
            if _fl:
                msg += "  [IN FORCE: the quantile — the outcome bar is benched]"
            roll_lines.append(msg)
        if events or roll_lines:
            lines.append("── THE ROLLING GRADE (pooled 4 weeks, the fit vs "
                         "the quantile, canonical axis) ──")
            lines.extend(roll_lines)
            for st_name, ev in events.items():
                if not ev:
                    continue
                if ev["action"] == "flip":
                    lines.append(f"  ⚡ {st_name} AUTO-FLIPPED to the quantile: "
                                 f"{ev['losing_weeks']} weeks with the fit below "
                                 f"it (pooled fit edge {ev['roll_delta']:+.2f}) "
                                 f"— tide_learn now ships the quantile there "
                                 f"until the outcome bar earns a flip-back")
                else:
                    lines.append(f"  ⚡ {st_name} FLIP-BACK: the outcome bar's "
                                 f"counterfactual edge won "
                                 f"{ev['winning_weeks']} weeks running "
                                 f"(pooled fit edge {ev['roll_delta']:+.2f}) "
                                 f"— it returns to live duty")
            lines.append("")
        blob_new = "\n".join(lines) + "\n"
        try:
            prev = OUT_FILE.read_text(encoding="utf-8") if OUT_FILE.exists() else ""
        except Exception:
            prev = ""
        blob = blob_new + prev
        if len(blob.encode("utf-8")) > REPORT_MAX_BYTES:
            blob = blob[:REPORT_MAX_BYTES]
        OUT_FILE.write_text(blob, encoding="utf-8")
        # the JSON sidecar (atomic swap): the console's MASTER verdict
        # plate reads this instead of parsing the prose report
        try:
            import os
            _jtmp = str(JSON_FILE) + ".tmp"
            with open(_jtmp, "w", encoding="utf-8") as jf:
                json.dump(summary, jf, indent=1)
            os.replace(_jtmp, JSON_FILE)
        except Exception as exc:
            # the txt report is the record of note; a lost sidecar
            # only dims the MASTER plate until the next pass
            logger.warning("weekly JSON sidecar write failed (%s) — "
                           "the MASTER plate serves its cached copy", exc)
        with _lock:
            _last.update(ts=time.time(), summary=summary)
        logger.info("TIDE BAR WEEKLY: %s", blob_new.splitlines()[0])
        _grade_wake.set()   # the flip grade re-runs on the fresh ledger
        return summary
    except Exception as exc:
        with _lock:
            _last.update(ts=time.time(), summary={"err": str(exc)})
        logger.info("TIDE BAR WEEKLY: pass failed (%s) — the previous "
                    "report stands", exc)
        return {"err": str(exc)}


# ── THE OPP VERDICT CADENCE (Oct 2, the accumulation window's
# scheduled read): scratch/opp_hindsight.py grades the
# champion-opportunistic class under rules PRE-REGISTERED before its
# first fire (<10 graded = KEEP-PROVISIONAL; EV<=0 or 0-payers-at-n>=20
# = KILL; EV>0 but <50% of classic's = RETAIN-TIGHTEN; else KEEP) —
# but only when someone remembers to run it. The verdict now prints ON
# SCHEDULE: the driver runs the grader as a SUBPROCESS (zero API
# coupling — the untracked tool owns its method and ships its own
# bytes; the wiring never imports it), parses the verdict line + the
# opp census for the log, and PREPENDS the section to
# opp_hindsight_report.txt (the tide-bar convention: newest verdict
# always at the top, rotated at the family cap). A gone or broken
# grader fails SAFE — no section, the last report stands, never a
# fake verdict.
OPP_TOOL = ROOT / "scratch" / "opp_hindsight.py"
OPP_REPORT_FILE = ROOT / "opp_hindsight_report.txt"
OPP_SNAP_FILE = ROOT / "signal_snapshots.jsonl"      # the grader's own ledger read
OPP_CACHE_DB = ROOT / "ohlcv_cache.db"               # the grader's own candle read
OPP_VERDICT_FIRST_DELAY_S = 1200.0    # first read ~20 min after boot
OPP_VERDICT_PERIOD_S = 7 * 86_400.0   # weekly — the re-run clause the verdict itself states


OPP_VERDICTS = ("KEEP-PROVISIONAL", "KILL", "RETAIN-TIGHTEN", "KEEP")


def _parse_opp_verdict(out: str) -> dict:
    """Lift the pre-registered verdict and the opp census out of the
    grader's own stdout: the verdict is the '>>> ' line, and ONLY the
    four pre-registered verdicts count — any other '>>>' text is not
    the contract's verdict and parses as None (never saved as
    authoritative). The census is the champion-opportunistic class
    row's n= (optional — a changed row format costs the n, not the
    verdict's trust)."""
    verdict = None
    n = None
    for line in out.splitlines():
        s = line.strip()
        if s.startswith(">>> ") and verdict is None:
            tok = s[4:].strip().split()[0] if s[4:].strip() else ""
            if tok in OPP_VERDICTS:
                verdict = tok
        elif "champion-opportunistic" in s and "n=" in s and n is None:
            try:
                n = int(s.split("n=")[1].split()[0].strip())
            except (IndexError, ValueError):
                pass
    return {"verdict": verdict, "n": n}


def _append_opp_section(body: str = "") -> tuple[bool, str]:
    """One scheduled read of the opp verdict. When body is empty the
    untracked grader is RUN as a subprocess and its stdout is the
    section body (zero API coupling); a given body (tests, replays) is
    used verbatim. The section PREPENDS — the newest verdict is always
    at the top — and the file rotates at the family cap. Fail-safe at
    every door: a missing or failing grader, or output with no verdict
    line, writes NOTHING and the last report stands."""
    try:
        if not body:
            if not OPP_TOOL.exists():
                logger.info("OPP VERDICT: grader absent (untracked "
                            "scratch) — no section, the last report stands")
                return False, ""
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", str(OPP_TOOL)],
                capture_output=True, text=True, timeout=180, cwd=str(ROOT))
            if proc.returncode != 0:
                logger.info("OPP VERDICT: grader exited %d — no section",
                            proc.returncode)
                return False, ""
            body = proc.stdout
        parsed = _parse_opp_verdict(body)
        if not parsed.get("verdict"):
            logger.info("OPP VERDICT: no verdict line in the grader's "
                        "output — no section (never a fake verdict)")
            return False, ""
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        section = (f"=== THE OPP VERDICT — {stamp} ===\n"
                   f"verdict: {parsed['verdict']} (n={parsed.get('n')})\n"
                   + body.rstrip() + "\n\n")
        old = ""
        if OPP_REPORT_FILE.exists():
            try:
                old = OPP_REPORT_FILE.read_text(encoding="utf-8")
            except Exception:
                old = ""
        blob = section + old
        if len(blob.encode("utf-8")) > REPORT_MAX_BYTES:
            # BYTE-exact rotation (the reviewer minor, Oct 2): the cap is
            # measured in bytes, so the truncation ENCODES first, cuts
            # the BYTES, then decodes with invalid trailing bytes
            # ignored — a multibyte body can never leave the file over
            # the cap (slicing the STRING at the byte count cuts
            # characters, which encode back OVER the cap)
            blob = blob.encode("utf-8")[:REPORT_MAX_BYTES].decode(
                "utf-8", errors="ignore")
        # write BYTES, not text: write_text translates \n -> os.linesep
        # on Windows and would add bytes BACK after the byte-exact cut
        OPP_REPORT_FILE.write_bytes(blob.encode("utf-8"))
        logger.info("OPP VERDICT: %s (n=%s) — section prepended",
                    parsed["verdict"], parsed.get("n"))
        return True, section
    except Exception as exc:
        logger.info("OPP VERDICT: pass failed (%s)", exc)
        return False, ""


def opp_verdict_driver_loop(stop: threading.Event,
                            first_delay_s: float = OPP_VERDICT_FIRST_DELAY_S,
                            period_s: float = OPP_VERDICT_PERIOD_S):
    """The daemon loop: one read shortly after boot, then weekly (the
    cadence the verdict's own re-run clause states). Never raises; dies
    with the scanner — a broken pass never takes the funnel down."""
    stop.wait(first_delay_s)
    while not stop.is_set():
        try:
            _append_opp_section()
        except Exception as exc:
            logger.info("OPP VERDICT: loop pass failed (%s)", exc)
        stop.wait(period_s)


def weekly_driver_loop(stop: threading.Event,
                       first_delay_s: float = FIRST_DELAY_S,
                       period_s: float = PERIOD_S):
    """The daemon loop: one pass shortly after boot, then daily (the
    grade stays the trailing 7 days). Never raises; dies with the
    scanner."""
    stop.wait(first_delay_s)
    while not stop.is_set():
        try:
            report_now()
        except Exception as exc:
            logger.info("TIDE BAR WEEKLY: pass failed (%s)", exc)
        stop.wait(period_s)


def _event_aftermath(recs, weeks_list, ev, st, next_ev=None):
    """ONE flip event's aftermath, in dollars, pooled over the weeks
    that decision GOVERNED — the shared math both the flip grade and
    the console's audit trail render, so the txt and the plate can
    never disagree. The REALIZED side is the decision's own bar: after
    a flip the quantile ships (in force while benched — the model's
    bar_src becomes quantile), after a flip-back the fit returns to
    live duty. The COUNTERFACTUAL is the other seat over the same
    weeks (read by each record's own other_src), both in $/fire in
    the house convention. The event's decision weeks are excluded:
    the window starts the week after the newest evidence week and ends
    where the state's NEXT event takes over (next_ev), or at the end
    of history for an open decision. Either side one-sided = pending
    (no fake zero-rate is graded). Pure — no I/O. Returns:
      {status: 'pending', reason: ...} — no governed weeks yet, or
        they were one-sided;
      {status: 'graded', weeks, realized: {bar, fires, usd_per_fire},
       counterfactual: {bar, fires, usd_per_fire}, verdict} — verdict
       'the decision PAID' or 'the decision COST — the benched side
       would have paid better' (a tie pays the decision)."""
    act = str(ev.get("action") or "")
    ev_weeks = [d.get("week") for d in (ev.get("evidence") or [])]
    idxs = [i for i, w in enumerate(weeks_list) if w in ev_weeks]
    after = (max(idxs) + 1) if idxs else None
    end_idx = len(recs)
    if next_ev is not None:
        n_weeks = [d.get("week") for d in (next_ev.get("evidence") or [])]
        n_idxs = [i for i, w in enumerate(weeks_list) if w in n_weeks]
        if n_idxs:
            end_idx = max(n_idxs) + 1
    if after is None or after >= end_idx or after >= len(recs):
        return {"status": "pending",
                "reason": "no post-decision weeks graded yet"}
    # THE SEATS (Sep 12 FIX): the ledger's seats are REGIME-NORMALIZED
    # by each record's own other_src — in_force is always the bar the
    # decision put in charge and other is always the benched side.
    # (After a flip other_src becomes the outcome-fitted fit; after a
    # flip-back it returns to quantile — the regime tag travels with
    # the decision, per the fixture convention "quantile = the fit is
    # in force".) The old positional mapping (dec_i='other' after a
    # flip-back) only worked when a fixture contradicted its own
    # other_src, and mislabeled the quantile's dollars as the fit's on
    # every regime-right ledger — the false PAID the trial caught.
    dec_i, oth_i = "in_force", "other"
    dec_bar = ("quantile" if act == "flip" else "fit")
    cf_bar = ("fit" if act == "flip" else "quantile")
    dn, dd, on_, od = 0, 0.0, 0, 0.0
    for r in recs[after:end_idx]:
        s = (r.get("states") or {}).get(st) or {}
        a, b = s.get(dec_i) or {}, s.get(oth_i) or {}
        if a.get("fires"):
            dn += a["fires"]
            dd += a.get("usd", 0.0)
        if b.get("fires"):
            on_ += b["fires"]
            od += b.get("usd", 0.0)
    if not dn or not on_:
        # EITHER side one-sided = no grade: a fake $0.00 counterfactual
        # would manufacture a verdict out of missing evidence
        return {"status": "pending",
                "reason": "the post-decision weeks were one-sided"}
    dec_f = dd / dn
    oth_f = od / on_
    verdict = ("the decision PAID" if dec_f >= oth_f
               else "the decision COST — the benched side would have "
                    "paid better")
    return {"status": "graded", "weeks": len(recs[after:end_idx]),
            "weeks_win": [str(r.get("week") or "")
                          for r in recs[after:end_idx]],
            "realized": {"bar": dec_bar,
                         "fires": dn, "usd_per_fire": round(dec_f, 2),
                         "usd_total": round(dd, 2)},
            "counterfactual": {"bar": cf_bar,
                               "fires": on_,
                               "usd_per_fire": round(oth_f, 2),
                               "usd_total": round(od, 2)},
            "verdict": verdict}


def classify_decisions(recs, weeks_list, evs, flips):
    """THE ONE CLASSIFICATION (Sep 12): every trail decision graded
    ONCE, by the shared aftermath math — so the per-row chip, the
    verdict filter, and the census chip can never disagree with each
    other or with the dollars on the row. A decision with NO graded
    aftermath yet borrows its state's LIVE regime from the flips
    state: a flip whose bench is currently up classifies PAYING (its
    bench is paying), a flip-back whose re-arm is live classifies
    COSTING (its re-arm is paying) — the live strip's dollars extended
    to the decision that owns them. Everything else stays honestly
    pending. Pure — no I/O, no mutation. Returns (am_of, verdict_of,
    counts): am_of[idx] is the _event_aftermath dict (None for sims),
    verdict_of[idx] is 'PAYING' | 'COSTING' | 'pending', counts is
    {'PAYING': n, 'COSTING': n, 'pending': n}."""
    real_idx = {}
    for i, e in enumerate(evs):
        if not e.get("sim"):
            real_idx.setdefault(str(e.get("state") or "?"), []).append(i)
    am_of, verdict_of = {}, {}
    for i, e in enumerate(evs):
        st = str(e.get("state") or "?")
        act = str(e.get("action") or "?")
        am_of[i] = None
        if e.get("sim"):
            sa = e.get("sim_aftermath")
            if isinstance(sa, dict) and sa.get("status") == "graded":
                verdict_of[i] = ("PAYING" if str(sa.get("verdict", ""))
                                 .startswith("the decision PAID")
                                 else "COSTING")
            else:
                verdict_of[i] = "pending"
        else:
            sibs = real_idx.get(st) or [i]
            pos = sibs.index(i) if i in sibs else None
            nxt = (evs[sibs[pos - 1]]
                   if pos is not None and pos - 1 >= 0 else None)
            am = _event_aftermath(recs, weeks_list, e, st, next_ev=nxt)
            am_of[i] = am
            verdict_of[i] = (
                ("PAYING" if str(am.get("verdict", "")).startswith(
                    "the decision PAID") else "COSTING")
                if am.get("status") == "graded" else "pending")
        if verdict_of[i] == "pending":
            flipped_now = bool((flips.get(st) or {}).get("flipped"))
            if act == "flip" and flipped_now:
                verdict_of[i] = "PAYING"    # its live bench is paying
            elif act == "flip_back" and not flipped_now:
                verdict_of[i] = "COSTING"   # its live re-arm is paying
    counts = {"PAYING": 0, "COSTING": 0, "pending": 0}
    for v in verdict_of.values():
        counts[v] = counts.get(v, 0) + 1
    return am_of, verdict_of, counts


def verdict_census(counts):
    """THE CENSUS CHIP (Sep 12): the verdict counts rendered as the
    trail's and the plate's shared one-liner — 'n PAYING · n COSTING ·
    n pending', always in that order. One format, both surfaces, so
    the MASTER plate's chip and the trail's census line can never
    disagree. Pure."""
    return " · ".join(f"{counts.get(k, 0)} {k}"
                      for k in ("PAYING", "COSTING", "pending"))


# ── THE ROLLING RECKONING WINDOW (Sep 13) ──────────────────────────
def _load_reckoning_window():
    """The window ledger: one row per COMPLETED flip grade, its
    cumulative reckoning totals, and that grade's own contribution
    (the increment over the prior completed grade). Fail-open ([] — a
    young ledger has no window; the cluster flag falls back to the
    cumulative stamps, today's shape). Never raises."""
    out = []
    try:
        with open(RECKONING_WINDOW_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue   # a bad window line is skipped; the cap
                    # keeps the ledger bounded, health_check names rot
                if isinstance(r, dict):
                    out.append(r)
    except FileNotFoundError:
        return []   # no window yet = the young-ledger normal
    except Exception as exc:
        logger.warning("RECKONING WINDOW unreadable (%s) — the regime "
                       "flag falls back to the cumulative stamps", exc)
    return out


def _reckoning_window_view(rows=None, n=RECKONING_WINDOW_N):
    """The last n window rows, [{agree, disagree, d_agree,
    d_disagree}]. Pure; the rule reads contributions, never the
    cumulative tails."""
    return [{"agree": int(r.get("agree") or 0),
             "disagree": int(r.get("disagree") or 0),
             "d_agree": int(r.get("d_agree") or 0),
             "d_disagree": int(r.get("d_disagree") or 0)}
            for r in (rows if rows is not None
                      else _load_reckoning_window())[-n:]]


def _reckoning_cluster_from_window(view):
    """THE ROLLING RULE: over the window's CONTRIBUTIONS —
    disagreements must dominate AND span >= RECKONING_MIN_GRADES
    distinct grades. A one-off bad borrow (however loud, a burst
    confined to one grade) cannot flag alone; a persistent regime
    keeps re-flagging while its disagreements keep arriving, and
    healed grades slide out the back."""
    try:
        da = sum(int(v.get("d_agree") or 0) for v in view)
        dd = sum(int(v.get("d_disagree") or 0) for v in view)
        span = sum(1 for v in view if int(v.get("d_disagree") or 0) > 0)
        return bool(dd > da and span >= RECKONING_MIN_GRADES)
    except Exception as exc:
        # a malformed row must not flag a regime — but it must not do
        # it silently either
        logger.warning("RECKONING WINDOW row malformed (%s) — the flag "
                       "reads False this grade", exc)
        return False


def _record_reckoning_window(agree: int, disagree: int, ts: str):
    """A completed grade appends its cumulative totals; the row's
    contributions are the increment over the prior completed grade's
    totals. Deduped on the grade's ts (the re-grade wake must not
    double-count a week's reckoning), capped at the window length
    plus slack, written atomically with the trail's bounded-retry
    discipline. Fail-open: an unwritable window never blocks a
    grade. Never raises."""
    try:
        rows = _load_reckoning_window()
        prev = rows[-1] if rows else {}
        pa, pd = int(prev.get("agree") or 0), int(prev.get("disagree") or 0)
        if any(str(r.get("ts") or "") == str(ts)
               and int(r.get("agree") or 0) == int(agree)
               and int(r.get("disagree") or 0) == int(disagree)
               for r in rows):
            return   # the same grade re-recording itself — no double-count
        # (same ts, CHANGED totals = honest progression: a fast re-grade
        # whose stamps moved within the minute appends its new truth)
        rows.append({"ts": str(ts),
                     "agree": int(agree), "disagree": int(disagree),
                     "d_agree": int(agree) - pa,
                     "d_disagree": int(disagree) - pd})
        rows = rows[-(RECKONING_WINDOW_N + 8):]
        tmp = str(RECKONING_WINDOW_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        import os
        for attempt in range(3):
            try:
                os.replace(tmp, RECKONING_WINDOW_FILE)
                return
            except OSError:
                if attempt == 2:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except Exception as exc:
        # the window feeds the regime flag — a lost row delays the
        # regime's evidence, which must not happen quietly
        logger.warning("RECKONING WINDOW write failed (%s) — this "
                       "grade's contribution may not reach the next "
                       "regime view", exc)


def reckoning_tally(evs):
    """THE RECKONING TALLY (Sep 12): how the borrow's own calls have
    graded — 'n agreed · n disagreed' — appended to the verdict census
    on the plate chip and the trail line once REAL flip-backs carry
    their borrow_reckoning stamp (the hook stamps after the aftermath
    grades; until then there is nothing to tally and the census shows
    alone — no fake 0 · 0). Sims and non-flip_back records never
    count; a malformed stamp is skipped, not fatal. Pure; returns ''
    when nothing has graded."""
    agree = disagree = 0
    try:
        for e in (evs or []):
            if e.get("sim") or str(e.get("action") or "") != "flip_back":
                continue
            rk = e.get("borrow_reckoning")
            if not isinstance(rk, dict):
                continue
            st = rk.get("status")
            if st == "agree":
                agree += 1
            elif st == "disagree":
                disagree += 1
    except Exception as exc:
        # a malformed stamp under-counts the census — visible, so the
        # chip's numbers are never trusted blind
        logger.warning("reckoning stamp malformed in the trail (%s) — "
                       "the census may under-count", exc)
    if not agree and not disagree:
        return ""
    return f" · {agree} agreed · {disagree} disagreed"


def fbw3_counterfactual(recs, ev, state, am):
    """THE FBW=3 COUNTERFACTUAL (Sep 12): when a real flip-back's
    aftermath grades COSTING, the audit trail asks its own question —
    would ONE more re-arm week (FLIP_BACK_WEEKS=3, the diag_fbw3 dial)
    have saved it? This replays the SHIPPED gate over the same ledger
    with only the week count raised to 3 — the canonical edge series
    and the pooled $/fire reads are regime-normalized off the records'
    own seats, so the replay differs from the lived walk in exactly the
    streak length — and prices what the extra week does, in dollars:

      denied      the replay re-arms LATER than the lived re-arm (the
                  lived arm fired at 2 winning weeks, the replay held
                  for a 3rd). The delayed arm's realized $/fire over
                  the weeks it then governs vs the LIVED arm's realized
                  $/fire (am's own — the same dollars the row shows)
                  = saved_per_fire (positive = the extra week pays
                  better), scaled by the delayed arm's fires into
                  saved_total — the contract axis the size ladder
                  prices.
      never-arms  the replay NEVER re-arms inside the ledger (no third
                  winning week — arc 3's shape): the extra week denies
                  the re-arm entirely, so the save is the decision's
                  own avoided cost — am's counterfactual minus its
                  realized $/fire, over am's governed window, scaled
                  by am's realized fires.
      same-week   the replay re-arms on the SAME week as the lived one
                  (the pool guard, not the week count, held the lived
                  arm): the week dial is not what denied this arm;
                  nothing to price.
      delayed     the replay re-arms later but too few post-arm weeks
                  have traded to price the delayed arm honestly.
      None        ungradeable — no post-decision week or no graded
                  aftermath. Honest silence.

    Read-only: _evaluate_flips mutates only its own copy and the
    constants are restored in finally. The lived event stream comes
    from the persisted flip ledger (_load_flip_events), so a deny is
    recognized as a DIVERGENCE from what actually happened, never from
    a re-derived expectation — earlier lived re-arms the replay
    reproduces count as agreement, and an arm on the event's own week
    is the same-week case. The COSTING gating is the caller's policy
    (the trail annotates only decisions that cost); this helper is the
    pure replay. Returns {status, ...} per above or None."""
    global FLIP_BACK_WEEKS, FLIP_WEEKS, POOL_MIN
    if str(ev.get("action") or "") != "flip_back":
        return None   # the counterfactual is a re-arm question only
    if not (isinstance(am, dict) and am.get("status") == "graded"):
        return None
    ev_weeks = [d.get("week") for d in (ev.get("evidence") or [])]
    weeks_list = [r.get("week") or "" for r in recs]
    idxs = [i for i, w in enumerate(weeks_list) if w in ev_weeks]
    dec_i = (max(idxs) + 1) if idxs else None
    if dec_i is None or dec_i >= len(recs):
        return None
    r_r = am["realized"]["usd_per_fire"]
    # the lived event stream — the replay's denials are divergences
    # from THIS, never from a re-derived expectation. REAL events only:
    # a sim worked example must never count as a lived decision.
    try:
        lived_evs = _real_flip_events()
        # keyed (week, action) FOR THIS STATE ONLY — another state's
        # same-week flip_back is not this replay's lived decision
        lived = {(str(e.get("week") or ""), str(e.get("action") or ""))
                 for e in lived_evs
                 if str(e.get("state") or "") == state}
    except Exception:
        lived = set()
    real_fb, real_fw = FLIP_BACK_WEEKS, FLIP_WEEKS
    real_pm = POOL_MIN
    FLIP_BACK_WEEKS, FLIP_WEEKS, POOL_MIN = 3, real_fw, 0.0
    _set_learned_pool_min({})   # the old world shipped the GLOBAL bar
    try:
        arms_w, fl = [], {st: {"flipped": True}
                          for st in ("OUT", "FLAT", "RISING")}
        for i in range(1, len(recs) + 1):
            e_w, fl = _evaluate_flips(recs[:i], fl)
            e_st = e_w.get(state)
            if e_st and str(e_st.get("action") or "") == "flip_back":
                arms_w.append(recs[i - 1]["week"])
    finally:
        FLIP_BACK_WEEKS, FLIP_WEEKS, POOL_MIN = real_fb, real_fw, real_pm
        _set_learned_pool_min(None)
    ev_week = str(ev.get("week") or "")
    if ev_week in arms_w:
        # the FBW=3 replay re-arms exactly where this decision armed —
        # the extra week changes nothing; the loss is not the week
        # dial's to save
        return {"status": "same-week"}
    if not arms_w:
        # no third winning week anywhere in the ledger — the extra
        # week denies the re-arm entirely: the decision's own avoided
        # cost, already priced by the row's aftermath math
        saved = round(am["counterfactual"]["usd_per_fire"] - r_r, 2)
        return {"status": "never-arms", "weeks": am["weeks"],
                "saved_per_fire": saved,
                "saved_total": round(saved
                                     * am["realized"]["fires"], 2)}
    # the earliest arm the LIVED gate did not make = the deny
    arm_w = next((w for w in arms_w
                  if (w, "flip_back") not in lived), None)
    if arm_w is None or arm_w not in weeks_list:
        return None
    arm_i = weeks_list.index(arm_w)
    later = sorted(w for (w, act) in lived
                   if act == "flip_back" and w in weeks_list
                   and weeks_list.index(w) > arm_i)
    end_i = (weeks_list.index(later[0]) + 1) if later else len(recs)
    qn, qu = 0, 0.0
    for r in recs[arm_i + 1:end_i]:
        s = (r.get("states") or {}).get(state) or {}
        a = s.get("in_force") or {}
        if a.get("fires"):
            qn += a["fires"]
            qu += a.get("usd", 0.0)
    if not qn:
        # the delayed arm has not traded enough weeks to price honestly
        return {"status": "delayed", "arm_week": arm_w}
    f_r = round(qu / qn, 2)
    return {"status": "denied", "arm_week": arm_w,
            "weeks": end_i - (arm_i + 1),
            "delayed_fires": qn, "delayed_per_fire": f_r,
            "saved_per_fire": round(f_r - r_r, 2),
            "saved_total": round((f_r - r_r) * qn, 2)}


# THE POOL COUNTERFACTUAL'S BAR (Sep 13): the graduated candidate the
# flip-back audit prices — would raising the pool bar to +0.25 $/fire
# have denied a COSTING thin-pool re-arm? The same ladder the monthly
# grade prices, now asked per decision.
POOL_CF_MIN = 0.25


def poolcf_counterfactual(recs, ev, state, am,
                          pool_min: float = POOL_CF_MIN):
    """THE POOL-THRESHOLD COUNTERFACTUAL (Sep 13): a real flip-back's
    aftermath grades COSTING and its pool was thin — would RAISING the
    pool bar (POOL_CF_MIN, the pool-grade's graduated family) have
    denied it? This replays the SHIPPED gate over the same ledger with
    only the pool minimum raised — the streak series and the decision
    evidence are untouched, so the replay differs from the lived walk
    in exactly the pool bar — and prices the denial in dollars:

      denied      the raised bar held this re-arm and the replay
                  re-arms LATER: the delayed arm's realized $/fire
                  (the bench shipping the quantile) over the weeks it
                  then governs, vs the LIVED arm's realized $/fire
                  (am's own), scaled by the delayed arm's fires into
                  saved_total — the same contract axis the FBW=3
                  sibling prices (positive = the denial saved;
                  negative = the delay cost more than the bleed — the
                  bar denies the right decision badly).
      never-arms  the raised bar holds the bench for the whole ledger
                  (a streak completes, but never with a pool clearing
                  the bar — or no streak completes at all): the save
                  is the decision's own avoided cost — am's
                  counterfactual minus realized $/fire, scaled by its
                  realized fires.
      same-week   the replay re-arms on the SAME week — the lived
                  pool already cleared the raised bar (a fat pool, or
                  the state's learned bar beats                  the candidate): the
                  pool dial is not what would have denied this arm.
      delayed     the replay re-arms later but too few post-arm weeks
                  have traded to price honestly.
      None        ungradeable — a sim, a non-flip_back, no post-
                  decision week, or an ungraded aftermath.

    Read-only: _evaluate_flips mutates only its own copy; POOL_MIN,
    FLIP_BACK_WEEKS and the learned-bars override are pinned and
    restored in finally (the replay's world is the SHIPPED global bar
    raised by pool_min — a shipped learned bar must not leak in). The
    lived event stream comes from the persisted ledger (_real_flip_events):
    an arm the lived gate also made is agreement, never a divergence.
    The COSTING gating is the caller's policy; this helper is the pure
    replay. Returns {status, ...} per above or None."""
    global POOL_MIN, FLIP_BACK_WEEKS
    if ev.get("sim") or str(ev.get("action") or "") != "flip_back":
        return None   # worked examples and flips are not re-arm questions
    if not (isinstance(am, dict) and am.get("status") == "graded"):
        return None
    ev_weeks = [d.get("week") for d in (ev.get("evidence") or [])]
    weeks_list = [r.get("week") or "" for r in recs]
    idxs = [i for i, w in enumerate(weeks_list) if w in ev_weeks]
    dec_i = (max(idxs) + 1) if idxs else None
    if dec_i is None or dec_i >= len(recs):
        return None
    r_r = am["realized"]["usd_per_fire"]
    try:
        lived_evs = _real_flip_events()
        # keyed (week, action) FOR THIS STATE ONLY — a FLAT flip_back at
        # the same week must never read as this state's lived decision
        lived = {(str(e.get("week") or ""), str(e.get("action") or ""))
                 for e in lived_evs
                 if str(e.get("state") or "") == state}
    except Exception:
        lived = set()
    real_pm, real_fb = POOL_MIN, FLIP_BACK_WEEKS
    POOL_MIN = float(pool_min)
    _set_learned_pool_min({})   # the replay world ships the GLOBAL bar
    try:
        arms_w, fl = [], {st: {"flipped": True}
                          for st in ("OUT", "FLAT", "RISING")}
        for i in range(1, len(recs) + 1):
            e_w, fl = _evaluate_flips(recs[:i], fl)
            e_st = e_w.get(state)
            if e_st and str(e_st.get("action") or "") == "flip_back":
                arms_w.append(recs[i - 1]["week"])
    finally:
        POOL_MIN, FLIP_BACK_WEEKS = real_pm, real_fb
        _set_learned_pool_min(None)
    ev_week = str(ev.get("week") or "")
    if ev_week in arms_w:
        return {"status": "same-week",
                "pool": round(ev["roll_delta"], 2)
                if isinstance(ev.get("roll_delta"), (int, float)) else None}
    if not arms_w:
        saved = round(am["counterfactual"]["usd_per_fire"] - r_r, 2)
        return {"status": "never-arms", "weeks": am["weeks"],
                "saved_per_fire": saved,
                "saved_total": round(saved
                                     * am["realized"]["fires"], 2)}
    arm_w = next((w for w in arms_w
                  if (w, "flip_back") not in lived), None)
    if arm_w is None or arm_w not in weeks_list:
        return None
    arm_i = weeks_list.index(arm_w)
    later = sorted(w for (w, act) in lived
                   if act == "flip_back" and w in weeks_list
                   and weeks_list.index(w) > arm_i)
    end_i = (weeks_list.index(later[0]) + 1) if later else len(recs)
    qn, qu = 0, 0.0
    for r in recs[arm_i + 1:end_i]:
        s = (r.get("states") or {}).get(state) or {}
        a = s.get("in_force") or {}
        if a.get("fires"):
            qn += a["fires"]
            qu += a.get("usd", 0.0)
    if not qn:
        return {"status": "delayed", "arm_week": arm_w}
    f_r = round(qu / qn, 2)
    return {"status": "denied", "arm_week": arm_w,
            "weeks": end_i - (arm_i + 1),
            "delayed_fires": qn, "delayed_per_fire": f_r,
            "saved_per_fire": round(f_r - r_r, 2),
            "saved_total": round((f_r - r_r) * qn, 2)}


def grade_regime_watch(recs):
    """THE WATCHER'S OWN GRADE (Sep 12): the regime watch's forecast
    (slope + pooled-series streak proxy) judged against the ACTUAL gate
    decisions (_evaluate_flips) week by week over the ledger. The
    watcher names 'gate due now' from the pooled-delta series' sign
    streak; the gate demands the per-week fit-edge streak AND the
    pooled $/fire delta on its side — this grade measures how often
    the two agree at the same week, when the proxy was EARLY (a
    due-now week where the gate stayed quiet — the pool not yet on
    the gate's side) and when it was LATE (the gate fired while the
    watcher still forecast weeks to go). Fair-play rules, both read
    off the live pass's own ordering: the watcher is graded on the
    PRE-DECISION phrase (report_now evaluates flips, THEN warns — at
    the firing week the phrase has already switched sides, so
    post-decision classification would miscount every real flip as
    late), and the walk seeds itself UN-flipped and replays the
    ledger's own deterministic gate — the same state sequence the
    lived regime walked. Pure — no I/O. Returns
    {state: {weeks_checked, agree, proxy_due_gate_quiet,
    gate_fired_proxy_not_due, forecasts: [{week, phrase, eta,
    gate_fired}]}} — forecasts keeps the last walk's phrases for the
    txt to render."""
    out = {}
    weeks_list = [r.get("week") or "" for r in recs]
    for st in ("OUT", "FLAT", "RISING"):
        # the trend EXACTLY as report_now builds it — the watcher must
        # be graded on the same series it forecasts from
        trend = []
        for i in range(len(recs)):
            r = _rolling_grade(recs[:i + 1], st)
            trend.append(None if r is None else r.get("delta"))
        agree = early = late = 0
        checked = 0
        forecasts = []
        # fair-play seed: the walk starts UN-flipped and replays the
        # ledger's own deterministic gate — seeding from the live flips
        # file would blind the walk to the lived regime's own history
        fl = {st: {"flipped": False}}
        for i in range(1, len(recs)):
            # the watcher's PRE-decision view: the flips state as it
            # stood ENTERING the week (flips_now in the live pass) — at
            # the firing week the post-decision state has already
            # switched sides, which would misclassify every agree as
            # late. Evaluate AFTER capturing the pre-state.
            pre_flips = fl
            evs_i, fl = _evaluate_flips(recs[:i + 1], fl)
            gate_fired = evs_i.get(st) is not None
            if sum(1 for v in trend[:i + 1] if v is not None) < 2:
                continue
            checked += 1
            watch = regime_watch({st: trend[:i + 1]}, pre_flips)
            phrase = str(watch.get(st) or "")
            proxy_due = "gate due now" in phrase
            if proxy_due and gate_fired:
                agree += 1
            elif proxy_due and not gate_fired:
                early += 1
            elif gate_fired and not proxy_due:
                late += 1
            eta = None
            if proxy_due:
                eta = 0
            elif "~" in phrase and "wk" in phrase:
                digits = "".join(ch for ch in
                                 phrase.split("~", 1)[1].split("wk")[0]
                                 if ch.isdigit())
                eta = int(digits) if digits else None
            forecasts.append({"week": weeks_list[i], "phrase": phrase,
                              "eta": eta, "gate_fired": gate_fired})
        out[st] = {"weeks_checked": checked, "agree": agree,
                   "proxy_due_gate_quiet": early,
                   "gate_fired_proxy_not_due": late,
                   "forecasts": forecasts}
    return out


def _open_bench_entries(recs, evs):
    """THE OPEN-BENCH LIVE VERDICT (Sep 12): every currently-benched
    state graded in dollars — the same shared _event_aftermath on the
    state's newest real flip with an open window (next_ev=None), so
    PAID/COST reads BEFORE the flip-back closes the decision. One math
    everywhere: the audit trail, this helper, and the flip grade's
    sidecar can never disagree. Returns (flips, entries) — flips is
    the loaded flips file, entries a list of
    {state, base, verdict, verdict_col, text, to_date}:
      verdict  'PAYING' | 'COSTING' | None (pending)
      text     the dollars line for graded benches, the shared math's
               own reason for pending ones
      to_date  the bench's CUMULATIVE dollars since the flip — what
               the quantile actually realized (usd_total) minus what
               the fit's counterfactual would have realized over the
               same weeks. Positive = the bench has PAID the book.
               None while pending.
    Pure — no I/O beyond the loaded arguments. Never raises."""
    try:
        flips = _load_flips() or {}
        weeks_list = [r.get("week") or "?" for r in (recs or [])]
        entries = []
        benched = [st for st in ("OUT", "FLAT", "RISING")
                   if (flips.get(st) or {}).get("flipped")]
        for st in benched:
            st_evs = [e for e in (evs or [])
                      if str(e.get("state") or "?") == st
                      and not e.get("sim")]
            newest_flip = next((e for e in st_evs
                                if str(e.get("action") or "") == "flip"),
                               None)
            base = (f"{st} — the quantile ships until the fit earns "
                    f"a flip-back")
            live = None
            if newest_flip is not None and recs:
                live = _event_aftermath(recs, weeks_list, newest_flip,
                                        st, next_ev=None)
            if live and live.get("status") == "graded":
                rl, cf = live["realized"], live["counterfactual"]
                paying = live["verdict"].startswith("the decision PAID")
                to_date = round(rl["usd_total"] - cf["usd_total"], 2)
                entries.append({
                    "state": st, "base": base,
                    "verdict": "PAYING" if paying else "COSTING",
                    "verdict_col": "#4be38a" if paying else "#ff6b5e",
                    "text": (f"over the {live['weeks']} weeks since, the "
                             f"quantile realized ${rl['usd_per_fire']:+.2f}"
                             f"/fire ({rl['fires']} fires) vs the fit's "
                             f"${cf['usd_per_fire']:+.2f}/fire over the "
                             f"same weeks — to date the bench is "
                             f"{'up' if to_date >= 0 else 'down'} "
                             f"${abs(to_date):,.2f} cumulative"),
                    "to_date": to_date})
            else:
                reason = ("no flip event on the trail yet"
                          if newest_flip is None
                          else (live or {}).get("reason")
                          or "no post-decision weeks graded yet")
                entries.append({"state": st, "base": base,
                                "verdict": None, "verdict_col": "#9aa0a6",
                                "text": f"pending — {reason}",
                                "to_date": None})
        return flips, entries
    except Exception:
        return (_load_flips() or {}), []


def _write_flipgrade_json(ts: str, summary: dict = None):
    """The open-bench sidecar: the live verdict rides _flipgrade.json
    (atomic-swapped) so the FLIP GRADE plate shows a CURRENTLY BENCHED
    state's PAID/COST without the audit expander. Written on BOTH
    grade_ledger paths — a held grade clears the file, so the plate
    never shows a stale verdict from an earlier history. THE POOL-GATE
    SIDEcar (Sep 13): when the completed grade passes its summary, the
    pool-gate blocks travel too (the dial's per-threshold walked family
    net, the arc-3 default's verdict) so MASTER reads them without the
    txt; hold/fail paths omit them — stale dial verdicts clear with
    stale bench dollars. Never raises."""
    import os
    try:
        recs = _load_history()
        evs = _load_flip_events()
        _flips, entries = _open_bench_entries(recs, evs)
        payload = {"generated_ts": ts, "states": _flips,
                   "open_bench": entries}
        if summary:
            for k in ("poolgate", "poolgate_family", "poolmin_default",
                      "poolmin_learned", "poolgate_streak",
                      "fbwgate_family", "fbw_default"):
                if summary.get(k) is not None:
                    payload[k] = summary[k]
        tmp = str(FLIPGRADE_JSON) + ".tmp"
        Path(tmp).write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, FLIPGRADE_JSON)
    except Exception as exc:
        # THE STALE-VERDICT RULE: on any failure the sidecar clears so
        # the plate can never serve a stale PAID/COST — the clear's own
        # failure is the last thing that may hide
        try:
            if FLIPGRADE_JSON.exists():
                FLIPGRADE_JSON.unlink()
        except Exception as exc2:
            logger.warning("FLIPGRADE sidecar write AND clear failed "
                           "(%s / %s) — the plate may serve a stale "
                           "open-bench verdict", exc, exc2)


def _poolgate_span_savings(recs, state, wa, wb_excl):
    """Tag-normalized dollars a denial banks over the half-open week
    span [wa, wb_excl): Σ (quantile $/fire − fit $/fire) × the FIT
    seat's fires. Post-flip-back the fit ships, so the fit's fires are
    the exposure the denial pulls from it — negative when the fit
    out-earned the quantile (a denial MISSES money)."""
    tot = 0.0
    for r in recs:
        w = str(r.get("week") or "")
        if w < wa or (wb_excl and w >= wb_excl):
            continue
        st_r = (r.get("states") or {}).get(state) or {}
        fit_live = (st_r.get("other_src")
                    or "quantile").startswith("quantile")
        f = st_r.get("in_force") or {}
        q = st_r.get("other") or {}
        f, q = (f, q) if fit_live else (q, f)
        if not f.get("fires") or not q.get("fires"):
            continue
        tot += ((q.get("usd_per_fire") or 0.0)
                - (f.get("usd_per_fire") or 0.0)) * f["fires"]
    return round(tot, 2)


def _gate_rearm_week(recs, st, after_week, pool_min=None,
                     min_weeks=None):
    """First week strictly after `after_week` the gate (with the dial
    overrides pinned) re-arms — None if never. THE SHARED WALK both
    dials' counterfactuals run through: the shipped branch fires, the
    pinned dial simply doesn't (pool_min pins POOL_MIN, min_weeks pins
    FLIP_BACK_WEEKS — exactly one differs from shipped per grade
    line), the bench stays standing, the walk continues. Both pins
    restored in finally."""
    global POOL_MIN, FLIP_BACK_WEEKS
    real_pm, real_fw = POOL_MIN, FLIP_BACK_WEEKS
    if pool_min is not None:
        POOL_MIN = pool_min
    if min_weeks is not None:
        FLIP_BACK_WEEKS = min_weeks
    _set_learned_pool_min({})   # the dial's world pins the GLOBAL bar
    try:
        fl = {st: {"flipped": True}}
        for i, r in enumerate(recs):
            if (str(r.get("week") or "") <= after_week):
                continue
            e_w, fl = _evaluate_flips(recs[:i + 1], fl)
            e_st = e_w.get(st)
            if e_st and str(e_st.get("action") or "") == "flip_back":
                return str(r.get("week") or "")
        return None
    finally:
        POOL_MIN, FLIP_BACK_WEEKS = real_pm, real_fw
        _set_learned_pool_min(None)


def _poolgate_family_walk(recs, seed_flips, pool_min, fbw=None):
    """One walk of the shipped gate over recs with the pool threshold
    substituted for the live dial — THE FAMILY SWEEP's engine. The
    shipped branch runs verbatim through _evaluate_flips (which reads
    the global POOL_MIN via _pool_min_for); a flip_back whose pool
    fails pool_min is DROPPED and the bench restored, so the state
    stays benched and a later clearing week re-fires naturally (a
    DELAYED re-arm emerges from the replay, never hand-computed).
    Flip decisions are untouched — this thresholds the re-arm side
    only. seed_flips is the bench state the walk STARTS from (the
    sweep's seed rule: benches can predate the ledger). Events return
    as dicts with state/week; the threshold is the ONLY difference
    between walks (the sweep pins the live dial to 0.0 inside — the
    same discipline _gate_rearm_week's finally-restore uses).
    THE FBW AXIS (Sep 13): the fbw kwarg pins FLIP_BACK_WEEKS for the
    walk's lifetime — re-arms demanding MORE winning weeks than the
    shipped gate simply don't fire (the gate's own arithmetic), so a
    raised FBW's denied re-arms emerge from the same replay. The pool
    axis and the fbw axis are never mixed in one walk: a caller
    pricing FBW passes pool_min=0.0 and fbw=<candidate>."""
    global POOL_MIN, _LEARNED_POOL_MIN_OVERRIDE, FLIP_BACK_WEEKS
    real_pm = POOL_MIN
    real_lpm = _LEARNED_POOL_MIN_OVERRIDE
    real_fb = FLIP_BACK_WEEKS
    POOL_MIN = 0.0
    _LEARNED_POOL_MIN_OVERRIDE = {}
    if fbw is not None:
        FLIP_BACK_WEEKS = fbw
    try:
        events, fl = [], {k: dict(v) for k, v in seed_flips.items()}
        for i, rec in enumerate(recs):
            past = recs[:i + 1]
            evs_w, fl = _evaluate_flips(past, fl)
            for st in ("OUT", "FLAT", "RISING"):
                e = evs_w.get(st)
                if not e:
                    continue
                if e["action"] == "flip_back" and pool_min > 0 \
                        and (e.get("roll_delta") or 0) < pool_min:
                    # THE THRESHOLD DENIES the re-arm: drop the event,
                    # restore the bench, keep walking — strict < so a
                    # pool exactly at the dial clears (the shipped
                    # gate's inclusive rd >= POOL_MIN).
                    fl[st] = {"flipped": True, "since": None,
                              "reason": ""}
                    continue
                events.append({"state": st, "week": str(rec.get("week") or ""),
                               "action": e["action"]})
        return events, fl
    finally:
        POOL_MIN = real_pm
        _LEARNED_POOL_MIN_OVERRIDE = real_lpm
        FLIP_BACK_WEEKS = real_fb


def _dial_seed_flips(recs, evs):
    """THE SEED RULE, shared by both dial sweeps (Sep 13): the trail's
    first event per state decides the bench state a walk STARTS from
    — but ONLY a PRE-LEDGER event is pre-history (a first event at or
    after the ledger's first week is an ON-STREAM flip the walk must
    reproduce itself). Returns (first_week, fl0)."""
    weeks_list = [str(r.get("week") or "") for r in recs]
    first_week = weeks_list[0] if weeks_list else ""
    seeds = {}
    for e in evs:
        st = str(e.get("state") or "")
        if st not in ("OUT", "FLAT", "RISING"):
            continue
        wk = str(e.get("week") or "")
        act = str(e.get("action") or "")
        cur = seeds.get(st)
        if cur is None or wk < cur[0]:
            seeds[st] = (wk, act)
    fl0 = {st: {"flipped": bool(seeds.get(st))
                and seeds[st][0] < first_week
                and seeds[st][1] == "flip"}
           for st in ("OUT", "FLAT", "RISING")}
    return first_week, fl0


def _dial_gov(recs, event_rows, st, start_flipped):
    """THE GOVERNANCE MAP for one state (the diag's machine, shared by
    both dial sweeps): the shipping bar derived week-by-week from the
    world's own events (an event at w ships from w+1 — a flip benches
    the fit, a flip-back arms it), starting from the SAME seed the
    walk started from — a bench can predate the ledger, and a map
    that ignores it inverts every polarity downstream."""
    evs_s = sorted((str(r.get("week") or ""), str(r.get("action") or ""))
                   for r in event_rows
                   if str(r.get("state") or "") == st)
    flipped, i, g = bool(start_flipped), 0, {}
    for r in recs:
        w = str(r.get("week") or "")
        g[w] = flipped
        while i < len(evs_s) and evs_s[i][0] == w:
            # the ACTION decides — never blind alternation
            flipped = evs_s[i][1] != "flip_back"
            i += 1
    return g


def _dial_price_row(recs, shipped_events, fl0, events):
    """THE GOVERNANCE PRICING for one walked world (shared by both
    dial sweeps): denied/delayed flip_backs vs the shipped stream,
    then the DIVERGENCE priced once — every week the worlds disagree
    contributes (threshold_$pf − shipped_$pf) × the fit seat's fires
    (the same exposure convention as _poolgate_span_savings), so a
    denial saving and a cascade costing price in ONE pass with no
    week counted twice. Returns {denied, delayed, net, weekly,
    priced}; the weekly series is what the rolling-window stability
    gate consumes."""
    ship_fb = [(e["week"], e["state"]) for e in shipped_events
               if e["action"] == "flip_back"]
    thr_fb = [(e["week"], e["state"]) for e in events
              if e["action"] == "flip_back"]
    denied = [x for x in ship_fb if x not in thr_fb]
    delayed = [x for x in thr_fb if x not in ship_fb]
    ship_gov = {st: _dial_gov(recs, shipped_events, st, fl0[st]["flipped"])
                for st in ("OUT", "FLAT", "RISING")}
    thr_gov = {st: _dial_gov(recs, events, st, fl0[st]["flipped"])
               for st in ("OUT", "FLAT", "RISING")}
    tot, n_div, weekly = 0.0, 0, {}
    for r in recs:
        w = str(r.get("week") or "")
        for st in ("OUT", "FLAT", "RISING"):
            st_r = (r.get("states") or {}).get(st)
            if not st_r or thr_gov[st].get(w) == ship_gov[st].get(w):
                continue
            fit_live = (st_r.get("other_src")
                        or "quantile").startswith("quantile")
            f = st_r.get("in_force") or {}
            q = st_r.get("other") or {}
            f, q = (f, q) if fit_live else (q, f)
            if not f.get("fires") or not q.get("fires"):
                continue
            diff = (f["usd_per_fire"] - q["usd_per_fire"]) * f["fires"]
            contrib = diff if not thr_gov[st][w] else -diff
            tot += contrib
            weekly[w] = weekly.get(w, 0.0) + contrib
            n_div += 1
    return {"denied": denied, "delayed": delayed,
            "net": round(tot, 2),
            "weekly": dict(sorted(weekly.items())),
            "priced": [{"kind": "governance", "weeks": n_div}]
            if n_div else []}


def _poolgate_family_sweep(recs=None, evs=None) -> dict:
    """THE POOL-GATE FAMILY SWEEP (Sep 13): the diag's walk, wired into
    the monthly flip grade. Unlike the stamp-based pool-gate grade —
    which prices each real re-arm in isolation from its decision-time
    stamp — the WALK re-runs the shipped gate week-by-week per
    graduated threshold, dropping any flip_back whose pool fails the
    dial and restoring the bench, so DELAYED re-arms EMERGE from the
    replay itself and a denial's effect on LATER re-arms (the cascade)
    is captured: threshold 0 must reproduce the lived events (the
    walk's own baseline check), and any divergence from the stamp
    grade is a finding, not a bug to swallow. Benches that pre-date
    the ledger are seeded from the trail's first event per state
    (walks start from the same state the lived stream actually had).
    Benign risk — global state is pinned through _gate_rearm_week's
    exact finally-restore discipline, so replay worlds never leak.
    Pure over its inputs (sims never enter — callers pass the real
    trail); returns {lines, per, family, baseline_ok, cascades} for
    grade_ledger to embed.

    THE SEED RULE (Sep 13): _evaluate_flips reads the bench state from
    its flips_now argument, but the lived stream's bench may PREDATE
    the ledger's first week record. For each state, the trail's first
    event decides the seed: flip first = benched from week 0 (the
    quantile has been shipping since before the ledger knows);
    flip_back first = the fit live from week 0. No seed = unflipped.
    The baseline check (threshold 0 must reproduce the lived events)
    verifies the seeds against the ledger's own lived stream."""
    if recs is None:
        recs = _load_history()
    if evs is None:
        evs = _real_flip_events()
    weeks_list = [str(r.get("week") or "") for r in recs]
    lines = ["-- THE POOL-GATE FAMILY SWEEP -- the walk, wired in: "
             "the gate re-run week-by-week per graduated threshold, "
             "denials dropping the re-arm and restoring the bench so "
             "delays and cascades EMERGE from the replay (threshold 0 "
             "must reproduce the lived events — the walk's baseline "
             "check)"]
    out = {"lines": lines, "per": {}, "family": {},
           "baseline_ok": True, "cascades": {}}

    # the seed rule: the trail's first event per state decides the
    # bench state the replay STARTS in — but ONLY a PRE-LEDGER event
    # is pre-history (the Sep 13 shared-arc catch: a first event at
    # or after the ledger's first week is an ON-STREAM flip the walk
    # must reproduce itself, so the replay starts unflipped; seeding
    # it benched made the walk blind to every lived first-flip)
    first_week = weeks_list[0] if weeks_list else ""
    seeds = {}
    for e in evs:
        st = str(e.get("state") or "")
        if st not in ("OUT", "FLAT", "RISING"):
            continue
        wk = str(e.get("week") or "")
        act = str(e.get("action") or "")
        cur = seeds.get(st)
        if cur is None or wk < cur[0]:
            seeds[st] = (wk, act)
    fl0 = {st: {"flipped": bool(seeds.get(st))
                and seeds[st][0] < first_week
                and seeds[st][1] == "flip"}
           for st in ("OUT", "FLAT", "RISING")}

    # the baseline: threshold 0 IS the shipped gate — its walk must
    # reproduce the lived events exactly (the diag's equivalence pin,
    # on the live ledger). Events BEFORE the ledger's first week are
    # pre-history: they seed the walk (a bench can predate the ledger)
    # and are not required to be reproduced — only in-ledger events
    # are, because the walk can only fire on weeks it walks.
    first_week = weeks_list[0] if weeks_list else ""
    ev_ids = [(str(e.get("state") or ""), str(e.get("action") or ""),
               str(e.get("week") or ""))
              for e in evs
              if str(e.get("week") or "") >= first_week]
    # THE LIVED ARCS (Sep 13, the maturity handover): once real
    # flip-backs exist, the section names each state's OWN lived arc —
    # the events the walk must reproduce are her stream's actual
    # regime history, not the diag's seeded worked examples.
    _lived = {}
    for e in evs:
        _st = str(e.get("state") or "")
        _key = (str(e.get("week") or ""), str(e.get("action") or ""))
        if _st in ("OUT", "FLAT", "RISING"):
            _lived.setdefault(_st, []).append(_key)
    for _st in ("OUT", "FLAT", "RISING"):
        _arc = sorted(_lived.get(_st) or [])
        if len(_arc) >= 2:
            lines.append(f"  LIVED ARC {_st}: "
                         + " -> ".join(f"{a}@{w}" for w, a in _arc))

    shipped_events, _fl_ship = _poolgate_family_walk(recs, fl0, 0.0)
    ship_ids = [(e["state"], e["action"], e["week"])
                for e in shipped_events]
    if ship_ids != ev_ids:
        out["baseline_ok"] = False
        lines.append(
            "  ⚠ BASELINE DIVERGENCE: the threshold-0 walk produced "
            f"{ship_ids if ship_ids else 'no events'} vs lived "
            f"{ev_ids if ev_ids else 'no events'} — the walk and the "
            "lived gate disagree (a seed mismatch or a gate drift; "
            "the sweep's pricing is UNRELIABLE until reconciled)")
        return out

    # one walk per graduated threshold — deny at fire, bench stands,
    # the walk continues (delays emerge); the row pricing rides the
    # shared governance core
    per = {}
    for pm in POOLGATE_THRESHOLDS:
        evs_pm, _ = _poolgate_family_walk(recs, fl0, pm)
        per[pm] = _dial_price_row(recs, shipped_events, fl0, evs_pm)
    out["per"] = per

    for pm in POOLGATE_THRESHOLDS:
        row = per[pm]
        if row["priced"]:
            lines.append(
                f"  {pm:+.2f}: {row['priced'][0]['weeks']} divergent "
                f"governance week(s) vs the shipped stream — net "
                f"${row['net']:+,.2f} "
                f"({'SAVED' if row['net'] >= 0 else 'MISSED'})")
        else:
            lines.append(f"  {pm:+.2f}: identical governance — "
                         "nothing to price")

    # (the governance pricing now rides _dial_price_row — the shared
    # core both dial sweeps run, extracted Sep 13)
    # cascade surfacing: a denial changing the regime path shows as
    # walked re-arms the lived stream never made — the pricing above
    # carried the dollars in the governance divergence; this names the
    # pattern so the txt shows the machinery saw it
    ship_fb_all = [(e["week"], e["state"]) for e in shipped_events
                   if e["action"] == "flip_back"]
    for pm, row in per.items():
        extra = sorted(set(row["delayed"]) - set(ship_fb_all))
        if extra:
            out["cascades"][pm] = extra
            lines.append(
                f"  {pm:+.2f} CASCADE: denial(s) changed the world — "
                "the walked stream re-arms where the lived one never "
                f"did ({', '.join(f'{s}@{w}' for w, s in extra)})")
    fam = {pm: row["net"] for pm, row in per.items()}
    out["family"] = fam
    parts = " · ".join(f"{pm:+.2f} ${fam[pm]:+,.2f}" for pm in POOLGATE_THRESHOLDS)
    lines.append(f"  FAMILY NET: {parts} — a denial saves when the "
                 "fit under-earned its governed window, misses when "
                 "it out-earned it (negative = the dial would have "
                 "cost money)")
    return out



def _fbwgate_family_sweep(recs=None, evs=None) -> dict:
    """THE STREAK-DIAL FAMILY SWEEP (Sep 13): the pool dial's walk,
    run on the FBW axis — the shipped gate re-played week-by-week per
    graduated FLIP_BACK_WEEKS candidate (3, 4), re-arms demanding
    MORE winning weeks simply not firing (the gate's own arithmetic
    via the walk's fbw pin), so denials and their cascades EMERGE
    from the replay. Baseline-checked: the shipped-FBW walk must
    reproduce the lived events; the seed rule and the governance
    pricing are the pool sweep's own (one lens, one convention — the
    extracted _dial_seed_flips/_dial_price_row core). Pure over its
    inputs (sims never enter); returns {lines, per, family,
    baseline_ok, cascades} for the grade to embed."""
    if recs is None:
        recs = _load_history()
    if evs is None:
        evs = _real_flip_events()
    lines = ["-- THE FBW-GATE FAMILY SWEEP -- the pool dial's walk on "
             "the streak axis: the gate re-run per graduated "
             "FLIP_BACK_WEEKS candidate, denied re-arms restoring the "
             "bench so delays and cascades emerge from the replay "
             "(the shipped-FBW walk must reproduce the lived events)"]
    out = {"lines": lines, "per": {}, "family": {},
           "baseline_ok": True, "cascades": {}}
    first_week, fl0 = _dial_seed_flips(recs, evs)
    ev_ids = [(str(e.get("state") or ""), str(e.get("action") or ""),
               str(e.get("week") or ""))
              for e in evs
              if str(e.get("week") or "") >= first_week]
    shipped_events, _fl_ship = _poolgate_family_walk(recs, fl0, 0.0)
    ship_ids = [(e["state"], e["action"], e["week"])
                for e in shipped_events]
    if ship_ids != ev_ids:
        out["baseline_ok"] = False
        lines.append(
            "  ⚠ BASELINE DIVERGENCE: the shipped-FBW walk "
            f"produced {ship_ids if ship_ids else 'no events'} vs "
            f"lived {ev_ids if ev_ids else 'no events'} — the walk "
            "and the lived gate disagree (the sweep's pricing is "
            "UNRELIABLE until reconciled)")
        return out
    per = {}
    for fw in FBWGATE_WEEKS:
        evs_fw, _ = _poolgate_family_walk(recs, fl0, 0.0, fbw=fw)
        per[fw] = _dial_price_row(recs, shipped_events, fl0, evs_fw)
    out["per"] = per
    for fw in FBWGATE_WEEKS:
        row = per[fw]
        if row["priced"]:
            lines.append(
                f"  FBW={fw}: {row['priced'][0]['weeks']} divergent "
                f"governance week(s) vs the shipped stream — net "
                f"${row['net']:+,.2f} "
                f"({'SAVED' if row['net'] >= 0 else 'MISSED'})")
        else:
            lines.append(f"  FBW={fw}: identical governance — "
                         "nothing to price")
    ship_fb_all = [(e["week"], e["state"]) for e in shipped_events
                   if e["action"] == "flip_back"]
    for fw, row in per.items():
        extra = sorted(set(row["delayed"]) - set(ship_fb_all))
        if extra:
            out["cascades"][fw] = extra
            lines.append(
                f"  FBW={fw} CASCADE: denial(s) changed the world — "
                "the walked stream re-arms where the lived one never "
                f"did ({', '.join(f'{s}@{w}' for w, s in extra)})")
    fam = {fw: row["net"] for fw, row in per.items()}
    out["family"] = fam
    parts = " · ".join(f"FBW={fw} ${fam[fw]:+,.2f}"
                             for fw in FBWGATE_WEEKS)
    lines.append(f"  FAMILY NET: {parts} — a longer streak demand "
                 "saves when the re-arms it denies cost money after "
                 "the fact, misses when they paid (negative = the "
                 "dial would have cost money)")
    return out

def grade_poolgate(recs=None, evs=None, window=None) -> dict:
    """THE POOL-GATE GRADE (Sep 12): the POOL_MIN dial judged on the
    REAL flip ledger, riding the monthly flip grade — for each
    graduated threshold, which real re-arms it would have DENIED at
    fire (the event's own persisted roll_delta against the dial) and
    what each denial is worth in dollars. THE STREAK DIAL (Sep 12)
    rides the same grade: each event's own decision-time winning_weeks
    stamp against the graduated FBW candidates — one decision, both
    dials priced from its own stamp, the sweep's two axes on live
    data. ONE FORMAT with the trail, ONE LENS everywhere (Sep 13, the
    shared-arc pin): an undenied window's savings price through
    _poolgate_span_savings over the event's own governed weeks — the
    per-week (cf $/fire − realized $/fire) × the fit seat's fires
    convention shared with the DELAY branch, the family walk, and the
    diag — so a denied COSTING re-arm SAVES and a denied PAYING one
    MISSES money, reported negative, same table. A threshold that re-arms later on
    the same stream is a DELAY, priced only to the delayed arm's week.
    THE ROLLING RECKONING WINDOW (Sep 13): the cluster flag reads the
    last RECKONING_WINDOW_N completed grades' CONTRIBUTIONS when a
    window ledger exists — a persistent disagreement regime separates
    from a one-off bad borrow (a burst confined to one grade never
    flags alone), and healed grades slide out the back. `window`
    overrides it explicitly (the tests' determinism); None with no
    ledger falls back to the cumulative stamps (today's shape — a
    young ledger grades nothing yet). Pure over its inputs (the sims
    never enter — _real_flip_events excludes them); returns
    {lines, thresholds, streak, reckoning} for grade_ledger to embed.

    THE BORROW'S EVIDENCE (Sep 12): each priced event also carries its
    borrow_reckoning stamp (agree/disagree) into the grade — a DENY or
    DELAY line names how the borrow's call graded, and when clustered
    disagreements dominate (≥3 graded reckonings, disagree > agree,
    clustering on a dial's own denials) the grade says so as EVIDENCE
    AGAINST that dial's current setting — evidence, never an auto
    move; the dial turns by her hand."""
    if recs is None:
        recs = _load_history()
    if evs is None:
        evs = _real_flip_events()
    weeks_list = [r.get("week") or "?" for r in recs]
    lines = ["-- THE POOL-GATE DIAL -- the shipped POOL_MIN judged on "
             "the real ledger (a denied COSTING re-arm SAVES, a denied "
             "PAYING one MISSES — negative, same table). The STREAK "
             "dial rides the same grade: each event's own decision-"
             "time winning_weeks against the FBW candidates"]
    out = {"lines": lines, "thresholds": {}}
    backs = [e for e in evs if str(e.get("action") or "") == "flip_back"]
    if not backs:
        lines.append("  no real flip-backs on the ledger yet — the dial "
                     "grades nothing (the seeded replays stay "
                     "simulations)")
        lines.append("  THE BORROW'S RECORD: none graded yet — the "
                     "tally appears when real flip-backs' aftermaths "
                     "grade")
        out["reckoning"] = {"graded": 0, "agree": 0, "disagree": 0,
                            "cluster": False, "window": []}
        # the streak's zero-evidence verdict is well-defined too — no
        # real re-arms means every graduated FBW denies nothing. It
        # travels so the plate renders BOTH dials beside each other
        # even on a young ledger (the shape the summary contract pins).
        per_fw = {fw: {"denied": 0, "delayed": 0, "net": 0.0,
                       "events": []} for fw in FBWGATE_WEEKS}
        lines.append("  THE STREAK VERDICT: " + " · ".join(
            f"FBW={fw} -> {per_fw[fw]['denied']} denied, "
            f"{per_fw[fw]['delayed']} delayed, "
            f"net ${per_fw[fw]['net']:+,.2f}"
            for fw in FBWGATE_WEEKS)
            + "  (no real flip-backs — the streak grades nothing)")
        out["streak"] = per_fw
        _lm = _load_pool_min_map()
        lines.append("  LEARNED BARS: "
                     + (" · ".join(f"{s} {_b:+.2f}"
                                   for s, _b in sorted(_lm.items()))
                        if _lm else
                        "none in force — every state rides the "
                        "global dial (the learner ships per-state "
                        "bars only when a state's own edge weeks "
                        "clear the evidence gates)"))
        out["learned"] = {}
        return out
    per = {pm: {"denied": 0, "delayed": 0, "net": 0.0,
                "events": []} for pm in POOLGATE_THRESHOLDS}
    per_fw = {fw: {"denied": 0, "delayed": 0, "net": 0.0,
                   "events": []} for fw in FBWGATE_WEEKS}
    # THE BORROW'S EVIDENCE: reckoning tallies overall and per dial —
    # kept OUT of the per dicts (their shape is the summary contract);
    # a dial's denials split agree/disagree so clustering is per-dial
    rk_tot = {"agree": 0, "disagree": 0}
    rk_by = {pm: {"agree": 0, "disagree": 0} for pm in POOLGATE_THRESHOLDS}
    rk_fw = {fw: {"agree": 0, "disagree": 0} for fw in FBWGATE_WEEKS}
    for e in backs:
        st = str(e.get("state") or "")
        ew = str(e.get("week") or "")
        rd = e.get("roll_delta")
        rd = float(rd) if isinstance(rd, (int, float)) else None
        ww = e.get("winning_weeks")
        ww = int(ww) if isinstance(ww, (int, float)) else None
        # THE BORROW'S EVIDENCE: the event's own reckoning stamp (the
        # pass hook writes it once the aftermath grades; only real
        # flip_backs carry one). Tallied regardless of pricing; the
        # clause rides only the priced DENY/DELAY lines.
        _rk = e.get("borrow_reckoning")
        rk_s = (str(_rk.get("status")) if isinstance(_rk, dict)
                and _rk.get("status") in ("agree", "disagree") else None)
        if rk_s:
            rk_tot[rk_s] += 1
        rk_clause = (f" — reckoning: the borrow {rk_s}d" if rk_s
                     else "")
        if rd is None and ww is None:
            lines.append(f"  flip_back {st}@{ew}: no decision-time "
                         "stamp (roll_delta + winning_weeks) — both "
                         "dials grade nothing for it (a pre-stamp "
                         "record; every fire has carried the stamp "
                         "since Sep 12)")
            continue
        # the window closes at the state's NEXT event of ANY action
        # (the same successor grade_ledger's aftermath loop passes)
        st_evs_all = [x for x in evs
                      if str(x.get("state") or "") == st
                      and str(x.get("week") or "") >= ew]
        nxt = (st_evs_all[st_evs_all.index(e) + 1]
               if len(st_evs_all) > st_evs_all.index(e) + 1 else None)
        am = _event_aftermath(recs, weeks_list, e, st, next_ev=nxt)
        if am.get("status") != "graded":
            lines.append(f"  flip_back {st}@{ew}: aftermath pending — "
                         "the dial's denial is unpriced yet")
            continue
        rl, cf = am["realized"], am["counterfactual"]
        # ONE CONVENTION (Sep 13, the shared-arc catch): the deny
        # prices through _poolgate_span_savings over the aftermath's
        # own governed weeks — the SAME function, hence the SAME
        # per-week (cf $/fire − realized $/fire) × the fit seat's
        # fires lens as the DELAY branch above, the family walk, and
        # the diag. The old total-dollars lens mixed the seats'
        # VOLUME difference into the dial's dollars (pooled-rate ×
        # total fires ≠ the per-week sum when the bars fire different
        # pool sizes) and inverted the sign against every other lens
        # on the same decision; volume is a sizing question, not a
        # gate question.
        _wv = am.get("weeks_win") or []
        if _wv:
            _wb = next((w for w in weeks_list if w > _wv[-1]), None)
            full_save = round(_poolgate_span_savings(
                recs, st, _wv[0], _wb), 2)
        else:
            full_save = 0.0
        # the denial tallies this event's reckoning against each dial
        # that actually denies/delays it — clustering reads these
        def _rk_tally(tmap, key):
            if rk_s:
                tmap[key][rk_s] += 1

        # the lived window's first week, for the DELAYED pricing
        wa = next((w for w in weeks_list if w > ew), None)
        # THE LEARNED BAR'S OWN LINE (Sep 13): when the event decided
        # under a per-state learned bar, that bar is priced too — its
        # denial rides the same counterfactual machinery as the
        # graduated family, so the monthly audit never grades a live
        # dial the report never mentions.
        _pm_ev = e.get("pool_bar")
        _pm_ev = float(_pm_ev) if isinstance(_pm_ev, (int, float)) else None
        if _pm_ev is not None and rd is not None and rd < _pm_ev:
            lines.append(
                f"  flip_back {st}@{ew} (pool {rd:+.2f}): its learned "
                f"bar {_pm_ev:+.2f} denies this re-arm today — priced "
                "here against the graduated family so her hand can "
                "confirm or retire it")
        for pm in POOLGATE_THRESHOLDS:
            if rd is None or rd >= pm:
                continue   # the dial passes this re-arm
            delay_w = _gate_rearm_week(recs, st, ew, pool_min=pm)
            if delay_w is not None:
                save = _poolgate_span_savings(recs, st, wa, delay_w) \
                    if wa else 0.0
                per[pm]["delayed"] += 1
                per[pm]["net"] += save
                _rk_tally(rk_by, pm)
                per[pm]["events"].append(
                    {"state": st, "week": ew, "kind": "delayed",
                     "delay_week": delay_w, "dollars": save,
                     **({"reckoning": rk_s} if rk_s else {})})
                lines.append(
                    f"  flip_back {st}@{ew} (pool {rd:+.2f}): {pm:+.2f} "
                    f"DELAYED it — the gate re-arms at {delay_w}; the "
                    f"denial banks ${save:+,.2f} until then"
                    + rk_clause)
            else:
                per[pm]["denied"] += 1
                per[pm]["net"] += full_save
                _rk_tally(rk_by, pm)
                per[pm]["events"].append(
                    {"state": st, "week": ew, "kind": "denied",
                     "dollars": full_save,
                     **({"reckoning": rk_s} if rk_s else {})})
                lines.append(
                    f"  flip_back {st}@{ew} (pool {rd:+.2f}): {pm:+.2f} "
                    f"DENIES it — "
                    + (f"saves ${full_save:+,.2f} over its governed "
                       f"window (the {rl['bar']} paid "
                       f"${rl['usd_per_fire']:+.2f}/fire vs the "
                       f"{cf['bar']}'s ${cf['usd_per_fire']:+.2f}/fire)"
                       if full_save >= 0 else
                       f"MISSES ${-full_save:,.2f} — the re-arm was "
                       f"PAYING (the {rl['bar']} realized "
                       f"${rl['usd_per_fire']:+.2f}/fire vs the "
                       f"{cf['bar']}'s ${cf['usd_per_fire']:+.2f}/fire)")
                    + rk_clause)
        # THE STREAK DIAL (Sep 12): the same event's decision-time
        # winning_weeks against the graduated FBW candidates — the
        # sweep's other axis on live data, priced in the same dollars
        for fw in FBWGATE_WEEKS:
            if ww is None or ww >= fw:
                continue   # the dial passes this re-arm
            delay_w = _gate_rearm_week(recs, st, ew, min_weeks=fw)
            if delay_w is not None:
                save = _poolgate_span_savings(recs, st, wa, delay_w) \
                    if wa else 0.0
                per_fw[fw]["delayed"] += 1
                per_fw[fw]["net"] += save
                _rk_tally(rk_fw, fw)
                per_fw[fw]["events"].append(
                    {"state": st, "week": ew, "kind": "delayed",
                     "delay_week": delay_w, "dollars": save,
                     **({"reckoning": rk_s} if rk_s else {})})
                lines.append(
                    f"  flip_back {st}@{ew} (streak {ww}w): FBW={fw} "
                    f"DELAYED it — the gate re-arms at {delay_w}; the "
                    f"denial banks ${save:+,.2f} until then"
                    + rk_clause)
            else:
                per_fw[fw]["denied"] += 1
                per_fw[fw]["net"] += full_save
                _rk_tally(rk_fw, fw)
                per_fw[fw]["events"].append(
                    {"state": st, "week": ew, "kind": "denied",
                     "dollars": full_save,
                     **({"reckoning": rk_s} if rk_s else {})})
                lines.append(
                    f"  flip_back {st}@{ew} (streak {ww}w): FBW={fw} "
                    "DENIES it — "
                    + (f"saves ${full_save:+,.2f} over its governed "
                       f"window (the {rl['bar']} paid "
                       f"${rl['usd_per_fire']:+.2f}/fire vs the "
                       f"{cf['bar']}'s ${cf['usd_per_fire']:+.2f}/fire)"
                       if full_save >= 0 else
                       f"MISSES ${-full_save:,.2f} — the re-arm was "
                       f"PAYING (the {rl['bar']} realized "
                       f"${rl['usd_per_fire']:+.2f}/fire vs the "
                       f"{cf['bar']}'s ${cf['usd_per_fire']:+.2f}/fire)")
                    + rk_clause)
    # THE BORROW'S EVIDENCE VERDICT: clustered disagreements ARE
    # evidence when grading the dials — when enough of the borrow's
    # calls have graded and disagreements dominate, each dial whose
    # OWN denials ride the disagreed borrows hears it (a clean dial is
    # never tarred by another's cluster). Evidence, never an
    # auto-move: the dials turn by her hand.
    _n_rk = rk_tot["agree"] + rk_tot["disagree"]
    lines.append("  THE BORROW'S RECORD: "
                 + (_n_rk and f"{_n_rk} graded reckoning(s) — "
                    f"{rk_tot['agree']} agreed, "
                    f"{rk_tot['disagree']} disagreed"
                    or "none graded yet — the tally appears when real "
                       "flip-backs' aftermaths grade"))
    # THE ROLLING WINDOW (Sep 13): an explicit `window` view (the
    # tests' determinism) rules when given; a window ledger rides the
    # grade_ledger path; without either, the cumulative stamps keep
    # today's shape (a young ledger has no window to read yet).
    if window is not None:
        _win = _reckoning_window_view(window, RECKONING_WINDOW_N)
    else:
        _ledger_rows = _load_reckoning_window()
        if _ledger_rows:
            _win = _reckoning_window_view(_ledger_rows, RECKONING_WINDOW_N)
            # THIS grade's contribution rides its own view: the current
            # cumulative over the last recorded row. A regime flags in
            # the grade that completes it, not one grade later — and a
            # zero contribution (a re-grade after unrelated ledger
            # churn) changes nothing.
            _prev = _ledger_rows[-1]
            _pd_a = rk_tot["agree"] - int(_prev.get("agree") or 0)
            _pd_d = rk_tot["disagree"] - int(_prev.get("disagree") or 0)
            if _pd_a or _pd_d:
                _win = (_win + [{"agree": rk_tot["agree"],
                                 "disagree": rk_tot["disagree"],
                                 "d_agree": _pd_a, "d_disagree": _pd_d}]
                        )[-RECKONING_WINDOW_N:]
        else:
            _win = None
    if _win is not None:
        _rk_cluster = _reckoning_cluster_from_window(_win)
        _wdd = sum(v["d_disagree"] for v in _win)
        _wda = sum(v["d_agree"] for v in _win)
        _wspan = sum(1 for v in _win if v["d_disagree"] > 0)
        lines.append(
            f"  THE WINDOW'S VERDICT: the last {len(_win)} grade(s) "
            f"added {_wda} agreed / {_wdd} disagreed across {_wspan} "
            f"grade(s) — the regime flag reads contributions, so a "
            f"one-off burst confined to a single grade cannot flag "
            "alone")
    else:
        _rk_cluster = bool(_n_rk >= 3 and rk_tot["disagree"] > rk_tot["agree"])
    if _rk_cluster:
        for _rkt, _lab in ((rk_by, "POOL_MIN"), (rk_fw, "FBW")):
            _bad = [k for k, v in _rkt.items() if v["disagree"] > v["agree"]]
            if not _bad:
                continue
            _names = "+".join(
                f"{k:+.2f}" if isinstance(k, float) else str(k)
                for k in _bad)
            lines.append(
                f"  ⚠ CLUSTERED DISAGREEMENTS — evidence against "
                f"{_lab}={_names}: its own denials ride disagreed "
                "borrows — where this dial held, the borrow's calls "
                "graded wrong more often than not")
    lines.append("  THE LEDGER VERDICT: " + " · ".join(
        f"{pm:+.2f} -> {per[pm]['denied']} denied, "
        f"{per[pm]['delayed']} delayed, net ${per[pm]['net']:+,.2f}"
        for pm in POOLGATE_THRESHOLDS)
        + "  (the shipped 0.0 denies nothing by construction)")
    # THE LEARNED BARS' VERDICT (Sep 13): each state's own bar priced
    # from its own decision-time pool_bar stamps — None = the state
    # never decided under a learned bar (thin, or floor-rounded) — so
    # the audit names the evidence her hand would confirm or retire.
    _learned_evs = {}
    for e in backs:
        _pv = e.get("pool_bar")
        if isinstance(_pv, (int, float)) and e.get("state"):
            _learned_evs.setdefault(str(e["state"]), []).append(e)
    _learned_states = sorted(_learned_evs)
    if _learned_states:
        for _lst in _learned_states:
            _st_evs = _learned_evs[_lst]
            _den = _del = 0
            _net = 0.0
            for e in _st_evs:
                _ew = str(e.get("week") or "")
                _rd = e.get("roll_delta")
                _rd = float(_rd) if isinstance(_rd, (int, float)) else None
                _pv = e.get("pool_bar")
                _pv = float(_pv) if isinstance(_pv, (int, float)) else None
                if _rd is None or _pv is None or _rd >= _pv:
                    continue
                _nxt_evs = [x for x in evs
                            if str(x.get("state") or "") == _lst
                            and str(x.get("week") or "") >= _ew]
                _nxt = (_nxt_evs[_nxt_evs.index(e) + 1]
                        if len(_nxt_evs) > _nxt_evs.index(e) + 1 else None)
                _am = _event_aftermath(recs, weeks_list, e, _lst,
                                       next_ev=_nxt)
                if _am.get("status") != "graded":
                    continue
                _sv = round((_am["counterfactual"].get("usd_total") or 0.0)
                            - (_am["realized"].get("usd_total") or 0.0), 2)
                _wa = next((w for w in weeks_list if w > _ew), None)
                _dw = _gate_rearm_week(recs, _lst, _ew, pool_min=_pv)
                if _dw is not None and _wa:
                    _del += 1
                    _net += _poolgate_span_savings(recs, _lst, _wa, _dw)
                elif _dw is None:
                    _den += 1
                    _net += _sv
            lines.append(
                f"  LEARNED BAR {_lst} {_st_evs[0]['pool_bar']:+.2f} -> "
                f"{_den} denied, {_del} delayed, net ${_net:+,.2f} "
                f"(priced from its own {len(_st_evs)} decision-time "
                "stamp(s))")
    else:
        lines.append("  LEARNED BARS: none in force — every state "
                     "rides the global dial (the learner ships per-state "
                     "bars only when a state's own edge weeks clear the "
                     "evidence gates)")
    lines.append("  THE STREAK VERDICT: " + " · ".join(
        f"FBW={fw} -> {per_fw[fw]['denied']} denied, "
        f"{per_fw[fw]['delayed']} delayed, net ${per_fw[fw]['net']:+,.2f}"
        for fw in FBWGATE_WEEKS)
        + "  (the shipped FBW=2 denies nothing by construction)")
    out["thresholds"] = per
    out["streak"] = per_fw
    out["learned"] = {
        str(s): {"bar": _learned_evs[s][0].get("pool_bar"),
                 "events": len(_learned_evs[s])}
        for s in _learned_states}
    out["reckoning"] = {"graded": _n_rk, "agree": rk_tot["agree"],
                        "disagree": rk_tot["disagree"],
                        "cluster": _rk_cluster,
                        "window": (_win if _win is not None else [])}
    return out


def recommend_fbw(recs=None, evs=None,
                  min_events: int = POOLMIN_DEF_EVENTS) -> dict:
    """THE FBW LIVE SWEEP (Sep 13): the streak dial's raise judged on
    lived evidence -- the pool dial's discipline (min_events+ priced
    real re-arms, a netting-positive candidate, and every 4-week and
    8-week rolling window staying positive) run on the FBW axis via
    _fbwgate_family_sweep. PROPOSAL-ONLY by nature: FLIP_BACK_WEEKS
    is a shipped constant, so a recommendation is ADVICE for her hand
    (a code change she makes deliberately) -- the machinery never
    edits itself, never stages a file, never moves the gate. Stamps
    mode (a baseline-diverged walk) prices each real re-arm's own
    denial through _gate_rearm_week's min_weeks pin but can never
    propose -- stamps cannot show where in time the dollars landed,
    and the rolling windows are the proof a raise must pass. Returns
    {lines, recommendation, n_priced, stable, proposal, mode}."""
    if recs is None:
        recs = _load_history()
    if evs is None:
        evs = _real_flip_events()
    weeks_list = [r.get("week") or "" for r in recs]
    lines = ["-- THE FBW LIVE SWEEP -- the streak dial's raise judged "
             "on lived evidence (a denial saves; a raise proposes only "
             f"on {min_events}+ priced re-arms, a netting-positive "
             "candidate, and every rolling window staying positive -- "
             "and it is a recommendation, never an auto-change)"]
    out = {"lines": lines, "recommendation": None, "n_priced": 0,
           "stable": None, "proposal": None, "mode": None}
    try:
        _fs = _fbwgate_family_sweep(recs, evs)
    except Exception as exc:
        _fs = {"baseline_ok": False, "family": {}, "per": {},
               "lines": [], "err": str(exc)}
    backs = [e for e in evs if str(e.get("action") or "") == "flip_back"]
    per = {fw: {"denied": 0, "delayed": 0, "net": 0.0}
           for fw in FBWGATE_WEEKS}
    if _fs.get("baseline_ok"):
        out["mode"] = "walked"
        lines.append("  MACHINERY: the walked family -- the gate "
                     "re-run per FBW candidate on the lived stream "
                     "(the shipped-FBW walk reproduces the lived "
                     "events), delays and cascades emerging from the "
                     "replay")
        for fw in FBWGATE_WEEKS:
            per[fw]["net"] = _fs.get("family", {}).get(fw, 0.0)
            row = (_fs.get("per") or {}).get(fw) or {}
            per[fw]["denied"] = len(row.get("denied") or [])
            per[fw]["delayed"] = len(row.get("delayed") or [])
        lines.extend([l for l in _fs.get("lines", [])
                      if " CASCADE" in l])
        for e in backs:
            st = str(e.get("state") or "")
            ew = str(e.get("week") or "")
            if ew not in weeks_list:
                continue
            st_evs_all = [x for x in evs
                          if str(x.get("state") or "") == st
                          and str(x.get("week") or "") >= ew]
            nxt = (st_evs_all[st_evs_all.index(e) + 1]
                   if len(st_evs_all) > st_evs_all.index(e) + 1 else None)
            am = _event_aftermath(recs, weeks_list, e, st, next_ev=nxt)
            if am.get("status") == "graded":
                out["n_priced"] += 1
    else:
        out["mode"] = "stamps"
        lines.append("  MACHINERY: decision-time stamps (the walk's "
                     "baseline did NOT reproduce the lived events -- "
                     "the walked family is withheld; the stamps still "
                     "price every real re-arm's own denial, but no "
                     "proposal can ship without the walked windows)")
        if _fs.get("err"):
            lines.append(f"  (the walk itself failed: {_fs['err']})")
        for e in backs:
            st = str(e.get("state") or "")
            ew = str(e.get("week") or "")
            ww = e.get("winning_weeks")
            ww = int(ww) if isinstance(ww, (int, float)) else None
            if ww is None or ew not in weeks_list:
                continue
            st_evs_all = [x for x in evs
                          if str(x.get("state") or "") == st
                          and str(x.get("week") or "") >= ew]
            nxt = (st_evs_all[st_evs_all.index(e) + 1]
                   if len(st_evs_all) > st_evs_all.index(e) + 1 else None)
            am = _event_aftermath(recs, weeks_list, e, st, next_ev=nxt)
            if am.get("status") != "graded":
                lines.append(f"  flip_back {st}@{ew}: aftermath "
                             "pending -- unpriced (the sweep waits "
                             "for the dollars)")
                continue
            out["n_priced"] += 1
            rl, cf = am["realized"], am["counterfactual"]
            full_save = round((cf.get("usd_total") or 0.0)
                              - (rl.get("usd_total") or 0.0), 2)
            wa = next((w for w in weeks_list if w > ew), None)
            for fw in FBWGATE_WEEKS:
                if ww >= fw:
                    continue   # this re-arm cleared the candidate
                delay_w = _gate_rearm_week(recs, st, ew, min_weeks=fw)
                if delay_w is not None and wa:
                    save = _poolgate_span_savings(recs, st, wa, delay_w)
                    per[fw]["delayed"] += 1
                    per[fw]["net"] += save
                    lines.append(f"  FBW={fw} DELAYS {st}@{ew} "
                                 f"({ww} winning week(s)) to {delay_w}: "
                                 f"the denial banks ${save:+,.2f}")
                elif delay_w is None:
                    per[fw]["denied"] += 1
                    per[fw]["net"] += full_save
                    lines.append(f"  FBW={fw} DENIES {st}@{ew} "
                                 f"({ww} winning week(s)): "
                                 f"${full_save:+,.2f}")
    priced = out["n_priced"]
    if priced == 0:
        lines.append("  no priced real re-arms yet -- the shipped FBW=2 "
                     "has no evidence to move on (the sweep re-runs "
                     "with each monthly grade)")
        return out
    verdicts = " \u00b7 ".join(
        f"FBW={fw} -> {per[fw]['denied']} denied, "
        f"{per[fw]['delayed']} delayed, net ${per[fw]['net']:+,.2f}"
        for fw in FBWGATE_WEEKS)
    turns = {fw: per[fw]["net"] for fw in FBWGATE_WEEKS
             if per[fw]["net"] > 0.0}
    if priced < min_events:
        lines.append(f"  {verdicts}")
        lines.append(f"  VERDICT: KEEP 2 -- only {priced} priced "
                     f"re-arm(s); the dial turns on {min_events}+ "
                     "(outvoting its own noise needs more lived "
                     "decisions)")
        return out
    if not turns:
        lines.append(f"  {verdicts}")
        lines.append("  VERDICT: KEEP 2 -- no candidate's denials "
                     "net positive on the lived ledger (the longer "
                     "streaks have cost more than they saved so far)")
        return out
    best = max(turns, key=lambda k: (turns[k], k))
    lines.append(f"  {verdicts}")

    # THE STABILITY GATE -- the same key the pool dial's raise passes:
    # the per-week contributions re-summed over the trailing 4-week
    # and 8-week spans of the candidate's own divergent weeks. Any
    # window at or below zero blocks: a dial that only paid in the
    # past is not a dial that pays now.
    def _windows(fw):
        wk = ((_fs.get("per") or {}).get(fw) or {}).get("weekly") or {}
        if not wk:
            return False, []   # no series = no windows = no raise
        weeks_sorted = sorted(wk)
        bad = []
        for span in (4, 8):
            lo = "" if span >= len(weeks_sorted) else weeks_sorted[-span]
            tot = sum(v for k, v in wk.items() if k >= lo)
            if tot <= 0.0:
                bad.append(f"{span}w ${tot:+,.2f}")
        return (not bad), bad

    ok, bad = _windows(best)
    out["stable"] = ok
    if not ok:
        if not bad:
            lines.append(
                f"  RAISE BLOCKED: FBW={best} nets positive in full "
                f"(${turns[best]:+,.2f}) but no per-week series exists "
                "to prove it pays through time (the walked windows are "
                "required) -- reconcile the walk baseline first")
        else:
            lines.append(
                f"  RAISE BLOCKED: FBW={best} nets positive in full "
                f"(${turns[best]:+,.2f}) but a rolling window is "
                f"negative ({' \u00b7 '.join(bad)}) -- a hot streak that "
                "ends negative must not raise the dial")
        return out
    out["recommendation"] = best
    out["proposal"] = {"fbw": best, "n_priced": priced,
                       "net": round(turns[best], 2),
                       "mode": out.get("mode")}
    lines.append(
        f"  PROPOSAL (recommendation only -- the dial never moves "
        f"itself): raise FLIP_BACK_WEEKS -> {best}, net "
        f"${turns[best]:+,.2f} across {priced} priced real re-arms, "
        "positive across the 4-week and 8-week rolling windows. "
        "Applying is a deliberate code change (FLIP_BACK_WEEKS in "
        "tidebar_weekly.py), made by her hand.")
    return out


def recommend_pool_min(recs=None, evs=None,
                       min_events: int = POOLMIN_DEF_EVENTS) -> dict:
    """THE ARC-3 LIVE SWEEP (Sep 13): the pool dial's raise PROPOSAL,
    judged on lived evidence once the real ledger carries a few real
    flip-backs. THE STABILITY GATE (Sep 13): a raise proposes only
    when the candidate's denied net stays positive across ROLLING
    WINDOWS — the sweep's per-week contribution series re-summed over
    the trailing 4-week and 8-week spans of its own divergent weeks.
    A hot streak that ends negative (the fit recovered where the
    denial benched it) nets positive in the full-ledger sum while
    losing money at the tail — the windows exist to catch exactly
    that, so any negative window blocks the raise outright.
    THE HAND-OFF (Sep 13): the recommendation is PROPOSAL-ONLY — the
    machinery never writes the default file, never moves the gate,
    and never WARNS; a raise applies by her hand (set pool_min in
    tide_bar_poolmin_default.json — the gate reads it on its next
    decision), with the sweep's priced evidence as the worked case.
    The stamps are the fallback when the walk's baseline diverges —
    and stamps cannot show WHERE in time a denial's dollars landed,
    so a stamps-mode ledger never proposes: reconcile the walk
    first. Read-only over its inputs; returns {lines, recommendation,
    n_priced, shipped, mode, stable, proposal}."""
    if recs is None:
        recs = _load_history()
    if evs is None:
        evs = _real_flip_events()
    weeks_list = [r.get("week") or "" for r in recs]
    lines = ["-- THE ARC-3 LIVE SWEEP -- the pool dial's raise "
             "judged on lived evidence (a denial saves; a raise "
             f"proposes only on {min_events}+ priced re-arms, a "
             "netting-positive candidate, and every rolling window "
             "staying positive — and it applies by hand, never "
             "automatically)"]
    out = {"lines": lines, "recommendation": None, "n_priced": 0,
           "shipped": False, "mode": None, "stable": None,
           "proposal": None, "staged": None}
    # THE STAGE'S LIFETIME (Sep 13): every run first clears the
    # previous stage — the stage is only ever the NEWEST grade's
    # cleared proposal, so a blocked or held re-price can never leave
    # a stale confirm button on the plate. A raise that clears the
    # gate re-stages below. (Replay worlds never touch the stage.)
    if _LEARNED_POOL_MIN_OVERRIDE is None:
        try:
            POOLMIN_STAGED_FILE.unlink()
        except OSError:
            pass   # no stage on disk is the normal steady state
    # THE LIVED MACHINERY: the walked family net when the baseline
    # holds — the diag's replay on the lived stream. The stamps are
    # the fallback (and the priced-events counter for both paths).
    try:
        _fs = _poolgate_family_sweep(recs, evs)
    except Exception as exc:
        _fs = {"baseline_ok": False, "family": {}, "lines": [],
               "per": {}, "err": str(exc)}
    backs = [e for e in evs if str(e.get("action") or "") == "flip_back"]
    per = {pm: {"denied": 0, "delayed": 0, "net": 0.0}
           for pm in POOLGATE_THRESHOLDS}
    if _fs.get("baseline_ok"):
        out["mode"] = "walked"
        lines.append("  MACHINERY: the walked family — the gate "
                     "re-run per candidate on the lived stream (the "
                     "threshold-0 walk reproduces the lived events), "
                     "delays and cascades emerging from the replay")
        for pm in POOLGATE_THRESHOLDS:
            per[pm]["net"] = _fs.get("family", {}).get(pm, 0.0)
            row = (_fs.get("per") or {}).get(pm) or {}
            per[pm]["denied"] = len(row.get("denied") or [])
            per[pm]["delayed"] = len(row.get("delayed") or [])
        # the walk's own per-event lines ride the txt (which re-arms
        # denied, which delayed, which cascaded), and the priced count
        # is the same definition as the stamp path: real flip-backs on
        # the ledger with a graded aftermath (the events whose denials
        # actually carried dollars)
        lines.extend([l for l in _fs.get("lines", [])
                      if " DENIES " in l or " DELAYS " in l
                      or " CASCADE" in l or "unpriced" in l])
        for e in backs:
            st = str(e.get("state") or "")
            ew = str(e.get("week") or "")
            if ew not in weeks_list:
                continue
            st_evs_all = [x for x in evs
                          if str(x.get("state") or "") == st
                          and str(x.get("week") or "") >= ew]
            nxt = (st_evs_all[st_evs_all.index(e) + 1]
                   if len(st_evs_all) > st_evs_all.index(e) + 1 else None)
            am = _event_aftermath(recs, weeks_list, e, st, next_ev=nxt)
            if am.get("status") == "graded" \
                    and isinstance(e.get("roll_delta"), (int, float)):
                out["n_priced"] += 1
    else:
        out["mode"] = "stamps"
        lines.append("  MACHINERY: decision-time stamps (the walk's "
                     "baseline did NOT reproduce the lived events — "
                     "the walked family is withheld; the stamps still "
                     "price every real re-arm's own denial)")
        if _fs.get("err"):
            lines.append(f"  (the walk itself failed: {_fs['err']})")
        for e in backs:
            st = str(e.get("state") or "")
            ew = str(e.get("week") or "")
            rd = e.get("roll_delta")
            rd = float(rd) if isinstance(rd, (int, float)) else None
            if rd is None or ew not in weeks_list:
                continue
            # the window closes at the state's NEXT event of ANY action
            # (the same successor rule grade_poolgate's aftermath loop
            # uses)
            st_evs_all = [x for x in evs
                          if str(x.get("state") or "") == st
                          and str(x.get("week") or "") >= ew]
            nxt = (st_evs_all[st_evs_all.index(e) + 1]
                   if len(st_evs_all) > st_evs_all.index(e) + 1 else None)
            am = _event_aftermath(recs, weeks_list, e, st, next_ev=nxt)
            if am.get("status") != "graded":
                lines.append(f"  flip_back {st}@{ew}: aftermath "
                             "pending — unpriced (the sweep waits "
                             "for the dollars)")
                continue
            out["n_priced"] += 1
            rl, cf = am["realized"], am["counterfactual"]
            full_save = round((cf.get("usd_total") or 0.0)
                              - (rl.get("usd_total") or 0.0), 2)
            wa = next((w for w in weeks_list if w > ew), None)
            for pm in POOLGATE_THRESHOLDS:
                if rd >= pm:
                    continue   # this re-arm cleared the candidate
                delay_w = _gate_rearm_week(recs, st, ew, pool_min=pm)
                if delay_w is not None and wa:
                    save = _poolgate_span_savings(recs, st, wa, delay_w)
                    per[pm]["delayed"] += 1
                    per[pm]["net"] += save
                    lines.append(f"  {pm:+.2f} DELAYS {st}@{ew} "
                                 f"(pool {rd:+.2f}) to {delay_w}: "
                                 f"the denial banks ${save:+,.2f}")
                elif delay_w is None:
                    per[pm]["denied"] += 1
                    per[pm]["net"] += full_save
                    lines.append(f"  {pm:+.2f} DENIES {st}@{ew} "
                                 f"(pool {rd:+.2f}): "
                                 f"${full_save:+,.2f}")
    priced = out["n_priced"]
    if priced == 0:
        lines.append("  no priced real re-arms yet — the shipped 0.0 "
                     "has no evidence to move on (the sweep re-runs "
                     "with each monthly grade)")
        return out
    verdicts = " · ".join(
        f"{pm:+.2f} -> {per[pm]['denied']} denied, "
        f"{per[pm]['delayed']} delayed, net ${per[pm]['net']:+,.2f}"
        for pm in POOLGATE_THRESHOLDS)
    turns = {pm: per[pm]["net"] for pm in POOLGATE_THRESHOLDS
             if per[pm]["net"] > 0.0}
    if priced < min_events:
        lines.append(f"  {verdicts}")
        lines.append(f"  VERDICT: KEEP 0.0 — only {priced} priced "
                     f"re-arm(s); the dial turns on {min_events}+ "
                     "(outvoting its own noise needs more lived "
                     "decisions)")
        return out
    if not turns:
        lines.append(f"  {verdicts}")
        lines.append("  VERDICT: KEEP 0.0 — no candidate's denials "
                     "net positive on the lived ledger (the bars have "
                     "cost more than they saved so far)")
        return out
    best = max(turns, key=lambda k: (turns[k], k))
    lines.append(f"  {verdicts}")

    # THE STABILITY GATE: the raise's second key — the per-week
    # contributions re-summed over the trailing 4-week and 8-week
    # spans of the candidate's own divergent weeks (anchored at the
    # last priced week: silent recent weeks carry no evidence either
    # way). Any window at or below zero blocks: a dial that only
    # paid in the past is not a dial that pays now.
    def _windows(pm):
        wk = ((_fs.get("per") or {}).get(pm) or {}).get("weekly") or {}
        if not wk:
            return False, []   # no series = no windows = no raise
        weeks_sorted = sorted(wk)
        bad = []
        for span in (4, 8):
            lo = "" if span >= len(weeks_sorted) else weeks_sorted[-span]
            tot = sum(v for k, v in wk.items() if k >= lo)
            if tot <= 0.0:
                bad.append(f"{span}w ${tot:+,.2f}")
        return (not bad), bad

    ok, bad = _windows(best)
    out["stable"] = ok
    if not ok:
        if not bad:
            lines.append(
                f"  RAISE BLOCKED: {best:+.2f} nets positive in full "
                f"(${turns[best]:+,.2f}) but no per-week series exists "
                "to prove it pays through time (the walked windows are "
                "required) — reconcile the walk baseline first")
        else:
            lines.append(
                f"  RAISE BLOCKED: {best:+.2f} nets positive in full "
                f"(${turns[best]:+,.2f}) but a rolling window is "
                f"negative ({' · '.join(bad)}) — a hot streak that "
                "ends negative must not raise the dial")
        return out
    # THE HAND-OFF (Sep 13): STAGE the evidence-backed proposal — the
    # gate itself moves only on her MASTER confirmation
    out["recommendation"] = best
    out["proposal"] = {"pool_min": best, "n_priced": priced,
                       "net": round(turns[best], 2),
                       "mode": out.get("mode")}
    try:
        POOLMIN_STAGED_FILE.write_text(json.dumps(
            {"pool_min": best, "n_priced": priced,
             "net": round(turns[best], 2), "mode": out.get("mode"),
             "ts": time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())},
            indent=2), encoding="utf-8")
        out["staged"] = True
    except Exception as exc:
        lines.append(f"  (staging failed: {exc} — txt-only proposal "
                     "this run)")
    lines.append(
        f"  PROPOSAL (STAGED — confirm-to-apply on the MASTER plate; "
        f"the machinery never moves the gate on its own): default "
        f"POOL_MIN -> {best:+.2f}, net ${turns[best]:+,.2f} across "
        f"{priced} priced real re-arms, positive across the 4-week "
        "and 8-week rolling windows. Confirming writes "
        "tide_bar_poolmin_default.json with a loud WARN; the stage "
        "clears on the next grade that fails to re-propose.")
    return out


def apply_staged_pool_min() -> dict:
    """THE CONFIRM-TO-APPLY (Sep 13): her MASTER click applies the
    STAGED proposal — the raise the lived arc-3 sweep priced across
    the full stability gate (min_events+ priced re-arms, positive
    net, every rolling window positive). Guarded on every axis: a
    pinned replay world refuses; the stage must parse and carry a
    sane pool_min; and the stage must still match the LIVE ledger's
    own sweep (a ledger that moved since the stage is stale — the
    next grade re-stages before anything applies). On success: the
    evidence default file is written (the gate's resolution order —
    learned bar > evidence default > shipped 0.0 — picks it up on
    its next decision), the scanner log WARNS loudly, the stage
    unlinks (a proposal applies once), and the applied record is
    returned. On any refusal: {applied: False, reason} — never
    raises, never applies half-way."""
    if _LEARNED_POOL_MIN_OVERRIDE is not None:
        return {"applied": False,
                "reason": "replay world pinned — refusing to apply"}
    try:
        raw = json.loads(POOLMIN_STAGED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"applied": False, "reason": "no staged proposal"}
    if not isinstance(raw, dict):
        return {"applied": False, "reason": "staged proposal unparseable"}
    pm = raw.get("pool_min")
    if not isinstance(pm, (int, float)) or not 0.0 < float(pm) <= 1.0:
        return {"applied": False, "reason": "staged pool_min out of "
                                            "range"}
    # freshness: the stage must still be what the live ledger proposes
    try:
        rp = recommend_pool_min()
    except Exception as exc:
        return {"applied": False, "reason": f"re-price failed: {exc}"}
    if rp.get("recommendation") is None \
            or abs(float(rp["recommendation"]) - float(pm)) > 1e-9:
        return {"applied": False,
                "reason": "stale — the live sweep no longer proposes "
                          "this bar; the next grade re-stages"}
    rec = rp.get("proposal") or {}
    try:
        POOLMIN_DEF_FILE.write_text(json.dumps(
            {"pool_min": float(pm), "n_priced": rec.get("n_priced"),
             "net": rec.get("net"), "mode": rec.get("mode"),
             "applied_by": "confirm-to-apply (MASTER)",
             "ts": time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())},
            indent=2), encoding="utf-8")
    except Exception as exc:
        return {"applied": False, "reason": f"persist failed: {exc}"}
    logger.warning(
        "TIDE BAR: POOL_MIN default %+.2f APPLIED BY CONFIRMATION "
        "(net $%+.2f across %s priced real re-arms, every rolling "
        "window positive) — the flip-back gate demands a recovery "
        "worth more before re-arming", float(pm),
        float(rec.get("net") or 0.0), rec.get("n_priced"))
    try:
        POOLMIN_STAGED_FILE.unlink()
    except OSError:
        pass   # an unlinked stage is the success state
    return {"applied": True, "pool_min": float(pm),
            "n_priced": rec.get("n_priced"), "net": rec.get("net")}


def grade_ledger() -> dict:
    """THE FLIP GRADE (Sep 11): the auto-flip judged on its OWN real
    ledger — the weekly history graded per state, the bench proximity,
    and every flip event's aftermath in dollars. Prepends to
    _flipgrade.txt and returns the summary dict; raises nothing
    (fail-open with an err summary)."""
    try:
        recs = _load_history()
        evs = _real_flip_events()   # sims never grade
        if len(recs) < FLIPGRADE_MIN_WEEKS:
            # the sidecar travels even on a hold — today it CLEARS the
            # stale open-bench verdict so the plate never lies
            _write_flipgrade_json(
                time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime()))
            return {"held": (
                f"{len(recs)} week(s) of history — the grade holds until "
                f"{FLIPGRADE_MIN_WEEKS} (the seeded replays in "
                "diag_flipgrade.py remain simulations)"),
                "weeks": len(recs)}
        weeks_list = [r.get("week") or "?" for r in recs]
        lines = ["=== THE FLIP GRADE — the bench machinery judged on its "
                 "own ledger ===",
                 f"graded at: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}"
                 f"  ({len(recs)} weeks: {weeks_list[0]}..{weeks_list[-1]})",
                 "canonical axis: fit edge = outcome-fitted $/fire − "
                 "quantile $/fire (> 0 the outcome bar pays better)",
                 ""]
        summary = {"generated_ts": lines[1].split(": ", 1)[1],
                   "weeks": len(recs), "states": {}}
        flips_now = _load_flips()
        for st in ("OUT", "FLAT", "RISING"):
            edges = [_week_fit_edge(r, st) for r in recs]
            two = [e for e in edges if e is not None]
            if not two:
                lines.append(f"{st}: no two-sided weeks — not graded")
                lines.append("")
                summary["states"][st] = None
                continue
            roll = _rolling_grade(recs, st)
            streak = 0
            for e in reversed(edges):
                if e is None or e >= 0:
                    break
                streak += 1
            wstreak = 0
            for e in reversed(edges):
                if e is None or e <= 0:
                    break
                wstreak += 1
            flipped = bool((flips_now.get(st) or {}).get("flipped"))
            dist = FLIP_WEEKS - streak if not flipped else None
            dist_b = FLIP_BACK_WEEKS - wstreak if flipped else None
            lines.append(f"{st}  ({len(two)} two-sided weeks; "
                         f"{'BENCHED — the quantile ships' if flipped else 'the fit is live'})")
            lines.append(f"  edges by week: "
                         + " ".join("—" if e is None else f"{e:+.2f}"
                                    for e in edges))
            lines.append(f"  pooled ({roll['weeks']}w): fit "
                         f"{roll['fit']['fires']} fires "
                         f"${roll['fit']['usd_per_fire']:+.2f}/fire vs "
                         f"quantile {roll['quantile']['fires']} fires "
                         f"${roll['quantile']['usd_per_fire']:+.2f}/fire — "
                         f"fit edge {roll['delta']:+.2f}")
            if not flipped:
                lines.append(f"  bench proximity: {streak}/{FLIP_WEEKS} "
                             f"losing weeks (flips at {FLIP_WEEKS} with a "
                             f"negative pool; {dist} to go)")
            else:
                lines.append(f"  flip-back proximity: {wstreak}/"
                             f"{FLIP_BACK_WEEKS} winning weeks (returns at "
                             f"{FLIP_BACK_WEEKS} with a pool clearing "
                             f"{_pool_min_for(st):+.2f}/fire; {dist_b} "
                             "to go)")
            # the aftermath of any events for THIS state — the shared
            # _event_aftermath math, so the txt and the audit trail on
            # the console read the same dollars. Each event's window
            # closes where the state's next event takes over.
            st_evs = [e for e in evs if e.get("state") == st]
            for i, e in enumerate(st_evs):
                nxt = st_evs[i + 1] if i + 1 < len(st_evs) else None
                am = _event_aftermath(recs, weeks_list, e, st, next_ev=nxt)
                if am["status"] == "pending":
                    lines.append(f"  ⚡ {e.get('action')} at "
                                 f"{e.get('week')}: {am['reason']} — the "
                                 f"aftermath is pending")
                    continue
                rl, cf = am["realized"], am["counterfactual"]
                lines.append(f"  ⚡ {e.get('action')} at {e.get('week')}: "
                             f"in the {am['weeks']} weeks after, the "
                             f"{rl['bar']} paid ${rl['usd_per_fire']:+.2f}/fire"
                             f" ({rl['fires']} fires) vs the {cf['bar']}'s "
                             f"counterfactual ${cf['usd_per_fire']:+.2f}/fire"
                             f" — {am['verdict']}")
            lines.append("")
            summary["states"][st] = {
                "edges": edges, "pooled": roll.get("delta"),
                "flipped": flipped}
        # THE WATCHER'S GRADE (Sep 12): the regime watch's forecast
        # judged against the gate's actual decisions over the same
        # ledger — does the streak proxy match the pooled-delta gate?
        try:
            wg = grade_regime_watch(recs)
            lines.append("-- THE WATCHER -- the regime watch's forecast "
                         "vs the gate's actual decisions (the proxy "
                         "measures CHANGE, the gate measures LEVEL — a "
                         "steady bleed never slopes, so late includes "
                         "weeks the watcher never named)")
            for st in ("OUT", "FLAT", "RISING"):
                w = wg.get(st) or {}
                if not w.get("weeks_checked"):
                    continue
                lines.append(
                    f"  {st}: {w['weeks_checked']} weeks checked — "
                    f"due-now weeks the gate also fired: {w['agree']}, "
                    f"early (due-now, gate quiet): "
                    f"{w['proxy_due_gate_quiet']}, "
                    f"late (gate fired first): "
                    f"{w['gate_fired_proxy_not_due']}")
                for fc in (w.get("forecasts") or [])[-4:]:
                    if not fc.get("phrase"):
                        continue
                    lines.append(
                        f"    {fc['week']}: {fc['phrase']}"
                        + ("   <- the gate fired" if fc["gate_fired"]
                           else ""))
            lines.append("")
            summary["watcher"] = wg
        except Exception as exc:
            logger.info("FLIP GRADE: watcher grade failed (%s)", exc)
        # THE POOL-GATE DIAL (Sep 12): the shipped POOL_MIN judged on
        # the real ledger — which graduated thresholds would have
        # denied which real re-arms, in dollars — rides the monthly
        # grade so the dial's evidence accumulates with it
        try:
            pg = grade_poolgate(recs, evs)
            lines.extend(pg["lines"])
            lines.append("")
            summary["poolgate"] = pg["thresholds"]
            summary["poolgate_streak"] = pg.get("streak", {})
            summary["poolgate_reckoning"] = pg.get("reckoning", {})
            summary["poolgate_learned"] = pg.get("learned", {})
            # THE WINDOW'S ROW (Sep 13): only a COMPLETED grade records
            # itself — the cumulative stamps feed the next grade's
            # regime view, contributions deduped on this grade's ts so
            # the re-grade wake cannot double-count a week's reckonings
            _rk_sum = pg.get("reckoning") or {}
            _record_reckoning_window(
                int(_rk_sum.get("agree") or 0),
                int(_rk_sum.get("disagree") or 0),
                summary.get("generated_ts") or "")
        except Exception as exc:
            logger.info("FLIP GRADE: pool-gate grade failed (%s)", exc)
            summary["poolgate"] = {"error": str(exc)}
        # THE POOL-GATE FAMILY SWEEP (Sep 13): the diag's walk, wired
        # into the monthly grade — the gate re-run per graduated
        # threshold so delays and cascades EMERGE from the replay,
        # priced in the trail's own dollars. Baseline-checked: the
        # threshold-0 walk must reproduce the lived events, so the
        # walk can never silently drift from the gate that actually
        # decided.
        try:
            fs = _poolgate_family_sweep(recs, evs)
            lines.extend(fs["lines"])
            lines.append("")
            summary["poolgate_family"] = {
                "net": fs.get("family", {}),
                "baseline_ok": fs.get("baseline_ok"),
                "cascades": {str(k): v for k, v in
                             (fs.get("cascades") or {}).items()}}
        except Exception as exc:
            logger.info("FLIP GRADE: family sweep failed (%s)", exc)
            summary["poolgate_family"] = {"error": str(exc)}
        # THE ARC-3 LIVE SWEEP (Sep 13): the pool dial's default judged
        # on the real re-arms each monthly run — a recommendation that
        # turns the shipped 0.0 only on lived evidence, loudly
        try:
            rp = recommend_pool_min(recs, evs)
            lines.extend(rp["lines"])
            lines.append("")
            summary["poolmin_default"] = {
                "recommendation": rp.get("recommendation"),
                "n_priced": rp.get("n_priced", 0),
                "stable": rp.get("stable"),
                "proposal": rp.get("proposal"),
                "mode": rp.get("mode")}
        except Exception as exc:
            logger.info("FLIP GRADE: arc-3 live sweep failed (%s)", exc)
        # THE FBW DIAL'S FAMILY SWEEP + RECOMMENDATION (Sep 13): the
        # streak dial gets the pool dial's discipline — the walk per
        # graduated candidate, then the rolling-window stability gate
        # on a raise. The recommendation is ADVICE (a code change by
        # her hand), never an auto-move.
        try:
            ffs = _fbwgate_family_sweep(recs, evs)
            lines.extend(ffs["lines"])
            lines.append("")
            summary["fbwgate_family"] = {
                "net": ffs.get("family", {}),
                "baseline_ok": ffs.get("baseline_ok"),
                "cascades": {str(k): v for k, v in
                             (ffs.get("cascades") or {}).items()}}
        except Exception as exc:
            logger.info("FLIP GRADE: fbw family sweep failed (%s)", exc)
            summary["fbwgate_family"] = {"error": str(exc)}
        try:
            rf = recommend_fbw(recs, evs)
            lines.extend(rf["lines"])
            lines.append("")
            summary["fbw_default"] = {
                "recommendation": rf.get("recommendation"),
                "n_priced": rf.get("n_priced", 0),
                "stable": rf.get("stable"),
                "proposal": rf.get("proposal"),
                "mode": rf.get("mode")}
        except Exception as exc:
            logger.info("FLIP GRADE: fbw live sweep failed (%s)", exc)
        # THE LEARNED BARS' EVIDENCE (Sep 13): the per-state pool bars
        # the weekly learner derives from each state's own pooled-edge
        # distribution, embedded so the monthly grade states — in one
        # place — which states EARNED their own bar and which stay on
        # the global dial for lack of evidence. Two truths, named
        # apart: IN FORCE = the gate's live map (the file the daily
        # pass ships); DERIVED = this grade's fresh verdict over the
        # same recs. Agreement is the steady state; a divergence names
        # the weekly pass as the writer so the two can never silently
        # disagree.
        try:
            _lm_now = _load_pool_min_map()
            _learner = learn_pool_min(recs)
            lines.append("-- THE LEARNED BARS -- each state's own "
                         "POOL_MIN from its pooled-edge distribution "
                         "(median of its weekly fit edges, quantized "
                         "up to the 0.25 family; IN FORCE = the gate's "
                         "live file, DERIVED = this grade's fresh "
                         "verdict)")
            for _st in ("OUT", "FLAT", "RISING"):
                _lv = _learner.get(_st) or {}
                _fresh = _lv.get("pool_min")
                _med = _lv.get("median")
                _meds = (f"{_med:+.2f}" if isinstance(_med, (int, float))
                         else "n/a")
                _base = (f"median {_meds} over "
                         f"{_lv.get('n_edges', 0)} edge weeks")
                if _st in _lm_now:
                    _note = "in agreement with the gate"
                    if _fresh is None:
                        _note = ("the current evidence floors it — the "
                                 "next weekly pass drops it")
                    elif abs(_fresh - _lm_now[_st]) > 1e-9:
                        _note = (f"the fresh verdict derives "
                                 f"{_fresh:+.2f} — the next weekly pass "
                                 "updates the file")
                    lines.append(
                        f"  {_st}: bar {_lm_now[_st]:+.2f} IN FORCE "
                        f"({_base}; {_note})")
                elif _fresh is not None:
                    lines.append(
                        f"  {_st}: {_fresh:+.2f} DERIVED THIS GRADE "
                        f"({_base}) — the next weekly pass ships it to "
                        "the gate")
                else:
                    lines.append(
                        f"  {_st}: global dial ({_base} — "
                        f"{_lv.get('reason') or 'no bar derived'})")
            lines.append("")
            summary["poolmin_learned"] = {
                "map": _lm_now,
                "learner": {s: {"pool_min": v.get("pool_min"),
                                "n_edges": v.get("n_edges"),
                                "median": v.get("median"),
                                "reason": v.get("reason")}
                            for s, v in _learner.items()}}
        except Exception as exc:
            logger.info("FLIP GRADE: learned-bars block failed (%s)", exc)
        blob_new = "\n".join(lines) + "\n"
        try:
            prev = FLIPGRADE_FILE.read_text(encoding="utf-8") \
                if FLIPGRADE_FILE.exists() else ""
        except Exception:
            prev = ""
        blob = blob_new + prev
        if len(blob.encode("utf-8")) > FLIPGRADE_MAX_BYTES:
            blob = blob[:FLIPGRADE_MAX_BYTES]
        FLIPGRADE_FILE.write_text(blob, encoding="utf-8")
        # the open-bench sidecar rides every completed grade (Sep 12),
        # carrying the pool-gate dial verdicts for the plate (Sep 13)
        _write_flipgrade_json(
            time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime()),
            summary=summary)
        with _lock:
            _last["flipgrade_ts"] = time.time()
        return summary
    except Exception as exc:
        logger.info("FLIP GRADE: pass failed (%s)", exc)
        _write_flipgrade_json(   # fail-open: freshness beats staleness
            time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime()))
        return {"err": str(exc)}


def flipgrade_driver_loop(stop: threading.Event,
                          first_delay_s: float = FLIPGRADE_FIRST_DELAY_S,
                          period_s: float = FLIPGRADE_PERIOD_S,
                          wake: threading.Event | None = None,
                          settle_s: float = FLIPGRADE_WAKE_SETTLE_S):
    """The bench machinery judged on its own ledger — monthly by
    default, and EVENT-DRIVEN too: shortly after (settle_s) each
    weekly tide-bar pass lands (report_now sets the wake), the grade
    re-runs so the aftermath dollars stay fresh between monthly runs.
    A wake whose grade-relevant ledger (week records + real events,
    ignoring pass timestamps) matches the newest graded signature is a
    logged no-op — unchanged history never multiplies sections. Every
    completed attempt pushes the monthly clock out by period_s, so a
    wake-driven run both refreshes the section and preserves the
    monthly cadence. Below FLIPGRADE_MIN_WEEKS of history the pass
    holds (nothing is written — the hold reason is visible in
    snapshot()); never raises; dies with the scanner."""
    wake = wake if wake is not None else _grade_wake

    def _ledger_sig():
        # the grade's INPUTS only: what the sections are graded from
        # (week records' grades + the real event keys), not the pass
        # timestamps a refresh rewrites every day
        recs = _load_history()
        evs = _real_flip_events()
        weeks = tuple(
            (r.get("week"),
             json.dumps(r.get("states"), sort_keys=True,
                        default=str))
            for r in (recs or []))
        ev_ids = tuple(sorted(
            (e.get("state"), e.get("action"), e.get("week"))
            for e in (evs or [])))
        return weeks, ev_ids

    next_run = time.monotonic() + first_delay_s
    last_sig = None   # the ledger the newest written section graded
    while not stop.is_set():
        # -- sleep until due; a wake (pass landed) or shutdown breaks in --
        while not stop.is_set():
            remaining = next_run - time.monotonic()
            if remaining <= 0:
                break
            if wake.wait(timeout=min(remaining, 5.0)):
                wake.clear()
                stop.wait(settle_s)   # the pass's remaining writes land
                break
        if stop.is_set():
            break
        # -- grade; a wake on an unchanged ledger is a logged no-op --
        try:
            sig = _ledger_sig()
            if sig == last_sig:
                logger.info("FLIP GRADE: wake no-op — the ledger is "
                            "unchanged since the newest section")
            else:
                s = grade_ledger()
                if s.get("held"):
                    logger.info("FLIP GRADE: held — %s", s["held"])
                elif s.get("err"):
                    logger.info("FLIP GRADE: pass failed (%s)", s["err"])
                else:
                    logger.info("FLIP GRADE: graded %s weeks of history",
                                s.get("weeks"))
                    last_sig = sig
            next_run = time.monotonic() + period_s
        except Exception as exc:
            logger.info("FLIP GRADE: pass failed (%s)", exc)
            next_run = time.monotonic() + period_s
    return


def snapshot() -> dict:
    """Diagnostics: the last pass, without recomputing."""
    with _lock:
        return {"last": dict(_last)}


if __name__ == "__main__":
    import pprint
    pprint.pp(report_now())
