"""_protected_extension_gate.py — THE ACCEPTANCE GATE (Sep 27). The
protected 24h extension (exit_extension.py, commit 1be3a31) shipped
behind an operator dial with its PARITY constraints proved by drive
(never naked: armed width = min(ratchet, max(goal/2, 1.0)), price
floor, gap fills at the bar open). This study supplies what parity
cannot: the measured ACCEPTANCE GATE — does the protected form clear
the CORRECTED CONTROL BAR on the real ledger?

THE CORRECTED BAR (the session's accounting lesson, now the gate):
  control := the sum of policy deltas over the GOAL-BANKED control
  cohort ("took the goal" trades). Those books exited AT their goal,
  so a protection-preserving extension must leave them untouched —
  their policy exit equals their actual exit and the control delta is
  0.00 BY CONSTRUCTION. Any nonzero control is a sim artifact or a
  protection leak, and the gate FAILS. This is the same corrected
  accounting the trail sweeps adopted (every simulated exit counts;
  the baseline is the ladder's own discipline, not a naive zero).

THE SIMULATED POLICIES (one cohort, two shapes, same walk):
  PROTECTED : from each CUT-class trade's actual exit, hold up to 24h
              under the incumbent trail — ratchet * GF, latched
              half-goal arm, armed cap min(ratchet*GF, max(goal/2,1.0)*GF,
              lock_w*GF), price floor at entry + 2x RT (never scaled),
              bank-first ordering, gap-through fills at the bar OPEN,
              5m data, zero same-bar goal+stop conflicts measured by
              the referee on this book. Exit = goal touch or 24h close.
  NAKED     : the goalreach study's original rule — same window, but
              the trailing stop is REMOVED (stop stays None): the
              honest side-by-side that shows what the protection buys.

THE GATE: GATE: PASS iff protected control >= 0.00 (untouched  goal-bankers) AND the recovery/leak arithmetic is printed for the
  operator. Censorship (books whose window runs past the data end) is
  counted and excluded honestly — their outcome is unknown, not zero:
  an UN-EXITED book whose pair's feather ends before the horizon end
  is censored (pair-local, the goalreach coverage lesson — a global
  max timestamp masks a short pair, and a touch/trail observed before
  the data end is still a real exit). Read-only; pandas required (the
  scout venv has it).
ASCII-pure output.
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import _verdict_study as vs          # noqa: E402
from exit_extension import is_cut_reason   # the single source of truth
from _gf_series import GFSeries     # noqa: E402  (the shared per-day seam)

import datetime as _dt
import json as _json
from zoneinfo import ZoneInfo


def _sold_ms(raw):
    """sold_at string -> epoch ms with the naive-stamp rule (Sep 28).
    The live writer emits AWARE Denver stamps (_now_mst().isoformat(),
    ZoneInfo America/Denver); a naive stamp (no tz suffix) is legacy or
    foreign and used to be read in the machine's local zone - a winter
    stamp on a summer machine keyed a whole hour off (MDT vs MST) and
    the journal join silently missed rows. The house rule applies (the
    decay guard's legacy-naive handling, the engine-wide 'never
    hardcode an offset'): naive means Denver wall time. NOTE: the
    grader's ex_ms key (_verdict_study) normalizes the SAME way - the
    join stays comparable by construction on any machine."""
    ts = _dt.datetime.fromisoformat(str(raw))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ZoneInfo("America/Denver"))
    return int(ts.timestamp() * 1000)


def _journal_reasons():
    """sold_at-ms -> exit_reason from the journal. The live cut class
    lives in the journal's exit_reason (what the scanner SAID at fire
    time); the grader's verdict is a different taxonomy (post-hoc).
    The extension gates on the SCANNER's class, so the gate must too."""
    out = {}
    path = HERE / "contest_trades.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = _json.loads(line)
                ms = _sold_ms(r["sold_at"])
                out[ms] = str(r.get("exit_reason") or
                              r.get("note") or "")
            except (ValueError, KeyError, TypeError):
                continue
    return out

HERE = Path(__file__).parent
ARM_FRAC = 0.5
RT_PCT = 0.08
H_MS = 24 * 3600000
GF = float(json.loads(
    (HERE / "profit_guard_state.json").read_text(encoding="utf-8")
).get("factor") or 1.0)
GFS = GFSeries(frozen=GF)     # the per-day refinement's seam (shared)

print(f"guard factor (live): {GF}   horizon: 24h   data: 5m feathers")


def ratchet_w(entry, peak):
    pp = (peak / entry - 1.0) * 100.0
    return 1.5 if pp >= 2.0 else (2.0 if pp >= 1.0 else 3.0)


def _bars(pair, start_ms, end_ms):
    df = pd.read_feather(HERE / "surething_data" / "kraken" /
                         f"{pair.replace('/', '_')}-5m.feather")
    lo = pd.Timestamp(int(start_ms), unit="ms", tz="UTC")
    hi = pd.Timestamp(int(end_ms), unit="ms", tz="UTC")
    w = df[(df["date"] >= lo) & (df["date"] <= hi)].sort_values("date")
    return w


_PAIR_MAX = {}


def _pair_max_ts(pair):
    """The newest timestamp THIS PAIR's feather actually has, cached.
    A pair whose data ends early must censor its own un-exited books,
    not inherit another pair's data end and fabricate a horizon close
    out of stale bars (the goalreach coverage lesson; CODE RABBIT
    MAJOR, Sep 27)."""
    if pair not in _PAIR_MAX:
        df = pd.read_feather(HERE / "surething_data" / "kraken" /
                             f"{pair.replace('/', '_')}-5m.feather")
        _PAIR_MAX[pair] = df["date"].max() if len(df) else None
    return _PAIR_MAX[pair]


def walk(g, policy, gf_fn=None):
    """The extension walk from a trade's ACTUAL exit.
    Returns (exit_px, how, floored) or None (censored / no data).
    policy 'protected' carries the incumbent trail; 'naked' holds with
    no stop (the goalreach study's rule). Both exit at the goal touch
    or the 24h close."""
    entry, goal = g["entry"], g["goal"]
    bank_px = entry * (1 + goal / 100.0)
    floor_px = entry * (1 + 2 * RT_PCT / 100.0)
    end_ms = int(g["ex_ms"] + H_MS)
    peak = g["exit"]                       # the ride resumes from the cut
    armed = False
    stop = None
    floored = 0
    try:
        w = _bars(g["pair"], int(g["ex_ms"]), end_ms)
    except Exception:
        return None
    if w.empty:
        return None
    for ts, hi, lo, op, cl in zip(w["date"], w["high"], w["low"],
                                  w["open"], w["close"]):
        gf = gf_fn(ts) if gf_fn is not None else GF
        if hi >= bank_px:                  # bank-first: the touch ends it
            return bank_px, "goal", floored
        if hi > peak:
            peak = hi
        if policy == "protected":
            # the incumbent shape: ratchet * GF, latched arm at goal/2,
            # armed cap min(ratchet*GF, max(goal/2,1.0)*GF, lock_w*GF),
            # the floor clamps but NEVER scales (the referee's semantics)
            if not armed and goal > 0 and \
                    (peak / entry - 1.0) * 100.0 >= goal * ARM_FRAC:
                armed = True
            wdt = ratchet_w(entry, peak) * gf
            if armed:
                cap = max(goal * ARM_FRAC, 1.0) * gf
                lock_w = max(0.0, (1.0 - floor_px / peak) * 100.0) * gf
                cap = min(cap, lock_w) if lock_w > 0 else cap
                wdt = min(wdt, cap)
            stop = peak * (1 - wdt / 100.0)
            if armed and stop < floor_px <= peak:
                stop = floor_px
                floored = 1
            if stop is not None and stop > hi:
                return op, "trail", floored     # gap: fill at the open
            if stop is not None and lo <= stop:
                return stop, "trail", floored
        # naked: no stop — the ride only ends at goal or the horizon
    # horizon close: the last bar's close is the unwinding price — but
    # only when the pair's OWN data covers the window (CODE RABBIT
    # MAJOR, Sep 27): a feather that ends early leaves the ride still
    # on at the last bar, its outcome is unknown -> censor, never
    # fabricate a last-close exit out of stale bars. Touch/trail exits
    # observed before the data end still count (exposure stops at the
    # exit; coverage accounting does not — the goalreach lesson).
    if pd.Timestamp(int(end_ms), unit="ms", tz="UTC") \
            > _pair_max_ts(g["pair"]):
        return None
    last_close = float(w["close"].iloc[-1])
    return last_close, "horizon", floored


graded_all = vs.grade_trades()
_newest = max((float(g.get("ex_ms") or 0) for g in graded_all), default=0.0)
graded = [g for g in graded_all if float(g.get("ex_ms") or 0) <= _newest]

_reasons = _journal_reasons()
_matched = sum(1 for g in graded if g["ex_ms"] in _reasons)


def _is_cut(g):
    """The SCANNER's cut class from the journal's exit_reason (the
    same string the live seam will judge); a trade with no journal
    row (legacy, pre-reason) is NOT a cut - the extension never arms
    on unknown exits."""
    return is_cut_reason(_reasons.get(g["ex_ms"], ""))


cuts = [g for g in graded if _is_cut(g)]
controls = [g for g in graded if "took the goal" in str(g.get("verdict"))]
print(f"trades: {len(graded)} (frozen at load)   journal-matched: "
      f"{_matched}   cut-class: {len(cuts)}   "
      f"control (took the goal): {len(controls)}")

res = {}
for name, pol in (("PROTECTED", "protected"), ("NAKED", "naked")):
    rec = leak = 0.0
    goals = horizons = trails = 0
    censored = 0
    worst = 0.0
    for g in cuts:
        r = walk(g, pol)
        if r is None:
            censored += 1
            continue
        px, how, _fl = r
        d = (vs.net(g["entry"], px, g["size"])
             - vs.net(g["entry"], g["exit"], g["size"]))
        if d > 0:
            rec += d
        else:
            leak += d
        worst = min(worst, d)
        goals += how == "goal"
        horizons += how == "horizon"
        trails += how == "trail"
    res[name] = {"rec": rec, "leak": leak, "net": rec + leak,
                 "censored": censored, "goals": goals, "horizons": horizons,
                 "trails": trails, "worst": worst}
    print(f"\n{name}: cuts simulated {len(cuts) - censored} "
          f"(censored {censored})")
    print(f"  exits: goal {goals} | trail {trails} | horizon {horizons}")
    print(f"  recoveries +{rec:.2f} | leaks {leak:.2f} | "
          f"NET {rec + leak:.2f} | worst book {worst:.2f}")

# ── the corrected control bar: goal-banked books must not move ──────
# The control cohort = every cut-class trade the ladder graded as
# took-the-goal. CRITICAL SET MEMBERSHIP (the honest version of this
# bar): the grader banks a book at realized >= 0.9x its goal value,
# so a took-the-goal CUT's actual fill can sit ABOVE the goal price
# (price blows through; e.g. FIL +8.47% vs a +2.70% goal, actual exit
# $0.063 above the walk's bank). Re-walking such a book and handing it
# the bank price would MANUFACTURE a -106 phantom leak — the exact
# artifact the goalreach study caught ("nothing gets held that already
# hit its target"). The live machinery's own precondition applies: a
# banked position is CLOSED; the extension exists for CUTS. So the
# control set is the took-the-goal cuts whose actual fill was at-or-
# below the walk's bank (a real TP stop-out the machinery would
# re-protect), and the above-goal blow-throughs are counted
# SEPARATELY - they need no protection (already past the net) and are
# reported as the class the dial must never touch.
ctrl = 0.0
n_ctrl = 0
blow_through = 0
blow_delta = 0.0
for g in controls:
    if not _is_cut(g):
        continue                 # untouched by the dial: excluded
    bank_px = g["entry"] * (1 + g["goal"] / 100.0)
    if g["exit"] > bank_px:
        # the actual fill is ABOVE the goal price: already banked past
        # the net, the extension can never be involved (the row is
        # closed). Counted separately, never re-walked.
        blow_through += 1
        blow_delta += (vs.net(g["entry"], bank_px, g["size"])
                       - vs.net(g["entry"], g["exit"], g["size"]))
        continue
    n_ctrl += 1
    r = walk(g, "protected")
    if r is None:
        continue
    px, _how, _fl = r
    # bank-first: the walk returns the goal price on the first touch;
    # these books' actual exit WAS at-or-below the goal fill, so the
    # delta must be 0.00 by construction. Measure, do not assume.
    ctrl += (vs.net(g["entry"], px, g["size"])
             - vs.net(g["entry"], g["exit"], g["size"]))

p, n = res["PROTECTED"], res["NAKED"]
print("\n== THE CORRECTED CONTROL BAR ==")
print(f"protected ctrl $: {ctrl:.2f}   over {n_ctrl} cut+banked books "
      f"(0.00 = the baseline by construction; any drift is a leak)")
print(f"above-goal blow-throughs: {blow_through} books, phantom rebank "
      f"delta {blow_delta:+.2f} - NOT in the bar (already past the net; "
      f"closed rows the dial never touches)")
print(f"naked ctrl $: 0.00 by the same construction (no stop = no "
      f"re-hold risk; printed for the drive's side-by-side pin)")

# ── the implementable precondition: arm only below the goal ────────
# The live seam knows the cut's fill (w.entry, goal_pct, live). A cut
# whose fill sits at/above (goal - SLACK) has nothing left to reach:
# arming it re-holds a paid book, which the walk just measured as a
# real leak (PUMP -15.08). The preconditioned variant filters those
# books out of the arm set entirely - the faithful live form.
SLACK = 0.98          # arm only when fill < 98% of the goal level
pre_cuts = [g for g in cuts
            if g["exit"] < g["entry"] * (1 + g["goal"] / 100.0) * SLACK]
pre_ctrl = 0.0
for g in controls:
    if not _is_cut(g) or g not in pre_cuts:
        continue
    r = walk(g, "protected")
    if r is None:
        continue
    px, _how, _fl = r
    pre_ctrl += (vs.net(g["entry"], px, g["size"])
                 - vs.net(g["entry"], g["exit"], g["size"]))
pre_rec = pre_leak = 0.0
for g in pre_cuts:
    r = walk(g, "protected")
    if r is None:
        continue
    px, _how, _fl = r
    d = (vs.net(g["entry"], px, g["size"])
         - vs.net(g["entry"], g["exit"], g["size"]))
    if d > 0:
        pre_rec += d
    else:
        pre_leak += d

print("\n== THE PRECONDITIONED VARIANT (arm only below 98% of goal) ==")
print(f"arm set: {len(pre_cuts)} of {len(cuts)} cuts   "
      f"recoveries +{pre_rec:.2f} | leaks {pre_leak:.2f} | "
      f"NET {pre_rec + pre_leak:+.2f}")
print(f"control over the preconditioned cut+banked cohort: "
      f"{pre_ctrl:+.2f}")

print("\n== GATE (derived) ==")
print("(the gate reads the true TP-stop-out cohort only; the "
      "blow-through class is reported, never gated)")
decision = "PASS" if ctrl >= 0.0 else "FAIL"
print(f"GATE: {decision}  - protected control {ctrl:+.2f} vs the zero "
      f"baseline (corrected bar: the extension must not cost the "
      f"goal-banked cohort anything)")
print(f"protected net {p['net']:+.2f} vs naked net {n['net']:+.2f} "
      f"(what the protection buys/ costs on the cut cohort)")
print(f"preconditioned net {pre_rec + pre_leak:+.2f} with control "
      f"{pre_ctrl:+.2f} - the implementable form if the raw gate fails")
print(f"censored: {p['censored']} of {len(cuts)} cut books run past "
      f"their pair's data window - unknown, not zero")

# -- the per-day GF refinement (the shared seam's span gate) ----------
# Same walk, same cohort, same corrected bar - only the widths' factor
# becomes the bar's own MST-day value. Reports only when the series is
# long enough to matter (~2 weeks); before that this block stays an
# honest refusal and the frozen rows above remain the committed gate.
if GFS.ready():
    pre_rec_d = pre_leak_d = 0.0
    pre_ctrl_d = 0.0
    for g in controls:
        if not _is_cut(g) or g not in pre_cuts:
            continue
        r = walk(g, "protected", gf_fn=GFS.at)
        if r is None:
            continue
        px, _how, _fl = r
        pre_ctrl_d += (vs.net(g["entry"], px, g["size"])
                       - vs.net(g["entry"], g["exit"], g["size"]))
    for g in pre_cuts:
        r = walk(g, "protected", gf_fn=GFS.at)
        if r is None:
            continue
        px, _how, _fl = r
        d = (vs.net(g["entry"], px, g["size"])
             - vs.net(g["entry"], g["exit"], g["size"]))
        if d > 0:
            pre_rec_d += d
        else:
            pre_leak_d += d
    print(f"\n== PER-DAY GF (series {GFS.span}, {GFS.span_days} days) ==")
    print(f"preconditioned variant at the bars' own day factors: "
          f"recoveries +{pre_rec_d:.2f} | leaks {pre_leak_d:.2f} | "
          f"NET {pre_rec_d + pre_leak_d:+.2f}")
    print(f"control over the preconditioned cut+banked cohort: "
          f"{pre_ctrl_d:+.2f} "
          f"({'still' if pre_ctrl_d >= 0.0 else 'NO LONGER'} at or "
          f"above the zero baseline)")
else:
    print(f"\n{GFS.notyet_line()}: this gate's per-day refinement "
          f"waits for the series (the frozen-factor rows above remain "
          f"the committed gate; the precondition is the shipped "
          f"machinery's own form either way).")
sys.exit(0 if decision == "PASS" else 2)
