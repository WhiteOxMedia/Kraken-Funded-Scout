"""Test THE TRAIL-WIDTH SWEEP CONTRACT (Sep 27): the armed-trail width
study (_trail_k_sweep.py) and its TP-preserving hybrid
(_hybrid_trail_sweep.py) are pinned here so the DIAL DECISION cannot
drift silently. Three contracts:

  1. THE SIM SHAPE — the widths swept are discipline.TRAIL_SIM_ABLE,
     the armed trail arms at 90% of the trade's goal (the live
     machinery's shape), and fees enter through net()'s RT_COST.
  2. THE GUARD SEMANTICS — trail_proposal may only propose a width
     with a POSITIVE per-trade edge whose took-the-goal CONTROL is not
     made worse ("a good dial must not break the control"); she is
     fail-soft silent otherwise. The hybrid's bank-first bar ordering
     (goal fill beats the trail within one bar) is what makes that
     control structurally satisfiable — the rule the first sweep
     lacked (every width broke the control before the hybrid).
  3. THE INCUMBENT DECISION RULE, EXECUTED ON FROZEN HISTORY — for
     every candidate width the hybrid walk must show no POSITIVE
     per-trade edge on the ride cohort with an UNTOUCHED control. The
     cohort is frozen AT LOAD (cutoff = newest exit present + 1 ms) so
     a trade stamped mid-drive cannot flip the verdict mid-run — the
     same mutant flipped 18/1 then 19/0 within minutes before this
     rule existed. While no width clears both bars the incumbent
     ratchet stands and this drive is green; the day one does, check
     3.3 TURNS RED ON PURPOSE — that is the tripwire demanding a
     human re-examine the dial with the numbers in hand, not a bug.

Section 4 pins the incumbent's own ratchet behavior (the dial the rule
protects). Read-only throughout: the ledger walk queries the graded
trades and OHLCV; nothing is written.
"""
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


import _verdict_study as vs            # noqa: E402
import discipline as disc              # noqa: E402

HERE = Path(__file__).parent
_disc_src = (HERE / "discipline.py").read_text(encoding="utf-8")
_vs_src = (HERE / "_verdict_study.py").read_text(encoding="utf-8")

print("== 1. THE SIM SHAPE ==")
check("1.1", disc.TRAIL_SIM_ABLE == (1.0, 1.5, 2.0, 3.0),
      "the sweep set is exactly the four widths the study sims")
check("1.2", "def _sim_armed(trades, P, goal_mult=0.9):" in _vs_src
      and 'arm = g["entry"] * (1.0 + g["goal"] * goal_mult / 100.0)' in _vs_src,
      "the armed trail arms at 90% of the trade's goal (the live "
      "post-goal machinery's shape)")
check("1.3", "size * RT_COST" in _vs_src,
      "fees enter through net()'s round-trip cost (no free money)")

print("== 2. THE GUARD SEMANTICS ==")
check("2.1", "if _n and _d > 0:" in _disc_src
      and "if _per_c < 0:" in _disc_src
      and "a good dial must not break the control" in _disc_src,
      "trail_proposal: positive per-trade edge required, and a width "
      "whose control got worse is SKIPPED (the guard that kept the "
      "incumbent through every sweep so far)")
check("2.2", "she stays silent rather than propose a dial" in _disc_src,
      "fail-soft silence is the documented no-edge behavior")
check("2.2b", disc.trail_proposal("no-such-pattern-xyz") is None,
      "LIVE: an unknown pattern proposes nothing (the silence path runs)")
with contextlib.redirect_stdout(io.StringIO()):
    import _hybrid_trail_sweep as hts   # noqa: E402  (module prints its tables)
check("2.3", hts.ARM_FRAC == 0.9,
      "the hybrid arms at the same 90% bar as _sim_armed (one arm "
      "semantics across both studies)")
_hsrc = (HERE / "_hybrid_trail_sweep.py").read_text(encoding="utf-8")
check("2.4", 0 < _hsrc.find('if hi >= goal:')
      < _hsrc.find("if hi > peak:"),
      "BANK-FIRST bar ordering in source: the goal fill is checked "
      "before the trail update within each bar — the rule that makes "
      "the control satisfiable (a banked trade can never trail out)")

print("== 3. THE INCUMBENT DECISION RULE, EXECUTED ON FROZEN HISTORY ==")
# REPRODUCIBILITY (Sep 27): the live ledger MOVES (the scanner stamps
# trades mid-drive — observed: the same mutant run flipped 18/1 then
# 19/0 within minutes). The decision rule is therefore evaluated on
# FROZEN history: the cohort is frozen AT LOAD — cutoff = the newest
# exit present + 1 ms — so a trade stamped mid-drive cannot change the
# verdict mid-run, and the cohort can never be empty (it is always at
# least the evidence the studies ruled on). The cohort grows only when
# genuine new trades have landed, which is exactly when re-evaluating
# the tripwire is correct; a red 3.3 then means NEW EVIDENCE, and a
# human re-examines the dial with the numbers in hand.
graded_all = vs.grade_trades()
_newest = max((float(g.get("ex_ms") or 0) for g in graded_all), default=0.0)
graded = [g for g in graded_all
          if float(g.get("ex_ms") or 0) <= _newest]
check("3.0", len(graded_all) >= 20 and len(graded) == len(graded_all),
      f"ledger readable ({len(graded_all)} trades; frozen cohort = the "
      f"full load, cutoff = newest exit + 1ms)")
_eligible = []             # widths clearing positive edge + the control bar
for _P in disc.TRAIL_SIM_ABLE:
    _rt = _banks = 0
    _tot = _rc = 0.0
    for _g in graded:
        _ex = hts.hybrid_exit(_g, _P)
        if not _ex:
            continue
        _px, _which = _ex
        _d = (vs.net(_g["entry"], _px, _g["size"])
              - vs.net(_g["entry"], _g["exit"], _g["size"]))
        _banks += _which == "bank"
        if "took the goal" in str(_g.get("verdict")):
            # CODE RABBIT MAJOR (Sep 27): EVERY simulated exit counts
            # toward the control — a bank that fills BELOW the ladder's
            # actual exit (the ladder rode past goal on some books)
            # worsens the control too; only counting trail exits could
            # report a clean control for an ineligible width.
            _rc += _d
        if _which == "trail":
            _tot += _d
            _rt += 1
    check(f"3.2.{_P:g}", _banks > 0,
          f"width {_P:g}: the bank-first rule actually banks on the "
          f"frozen cohort ({_banks} goals banked, {_rt} ride exits)")
    _per = _tot / _rt if _rt else 0.0
    if _rt and _per > 0:
        _eligible.append((_P, _per, _rc, _tot))

# THE FAIR CONTROL BAR (CodeRabbit major, Sep 27, corrected accounting):
# control deltas count EVERY simulated exit — banks included — and under
# that honest accounting NO bank-heavy policy is at zero (the goal-fill
# counterfactual alone is -54.24 vs the ladder's actual exits: the ladder
# rides past goal on control books). The bar is therefore RELATIVE: a
# candidate's control cost must not exceed the INCUMBENT RATCHET's own
# control cost under the identical walk. Zero remains the tiebreak dream,
# relative-no-worse is the enforceable contract.
def _ratchet_width(entry, peak):
    _pp = (peak / entry - 1.0) * 100.0
    return 1.5 if _pp >= 2.0 else (2.0 if _pp >= 1.0 else 3.0)


def _ratchet_exit_px(_g):
    _goal = _g["entry"] * (1 + _g["goal"] / 100.0)
    _arm = _g["entry"] * (1 + _g["goal"] * hts.ARM_FRAC / 100.0)
    _peak, _stop = _g["entry"], None
    for _ts, _hi, _lo in vs._db().execute(
            "SELECT ts, high, low FROM ohlcv WHERE pair=? AND ts>=? "
            "AND ts<=? ORDER BY ts",
            (_g["pair"], int(_g["en_ms"]),
             int(_g["ex_ms"] + vs.AFTER_MS))):
        if _hi >= _goal:
            return _goal
        if _hi > _peak:
            _peak = _hi
            if _peak >= _arm:
                _stop = _peak * (1 - _ratchet_width(_g["entry"], _peak) / 100.0)
        if _stop is not None and _lo <= _stop:
            return _stop
    return None


_rc_base = 0.0
for _g in graded:
    if "took the goal" not in str(_g.get("verdict")):
        continue
    _px = _ratchet_exit_px(_g)
    if _px is None:
        continue
    _rc_base += (vs.net(_g["entry"], _px, _g["size"])
                 - vs.net(_g["entry"], _g["exit"], _g["size"]))
_eligible = [e for e in _eligible if e[2] >= _rc_base - 1e-9]
check("3.3", not _eligible,
      "INCUMBENT DECISION RULE (frozen cohort, corrected control "
      "accounting): no width clears positive ride-edge WITHOUT a worse "
      "control cost than the incumbent ratchet's own under the same "
      f"walk (incumbent control baseline {_rc_base:+.2f}). IF THIS "
      "FAILS THE DATA HAS CHANGED — re-examine the dial: "
      f"{_eligible or 'no eligible width'}")
_live_banks = sum(bool((hts.hybrid_exit(_g, 3.0) or (None, None))[1] == "bank")
                  for _g in graded_all)
check("3.4", _live_banks > 0,
      f"LIVE ledger observation ({len(graded_all)} trades, "
      f"{_live_banks} banks at width 3.0): the walk executes on "
      f"today's book too — observation only, the ruling cohort is frozen")

print("== 4. THE INCUMBENT'S RATCHET (the dial the rule protects) ==")
import background_scanner as bs        # noqa: E402
check("4.1", bs.CONTEST_TRAIL_PCT == 3.0,
      "the base trail width is 3% off the running peak")
check("4.2", bs._ratchet_trail_pct(100.0, 100.9, 0) == 3.0,
      "below +1% peak: base 3.0 holds")
check("4.3", bs._ratchet_trail_pct(100.0, 101.0, 0) == 2.0
      and bs._ratchet_trail_pct(100.0, 101.9, 0) == 2.0,
      "at +1% peak the width ratchets to 2.0 (boundary inclusive)")
check("4.4", bs._ratchet_trail_pct(100.0, 102.0, 0) == 1.5
      and bs._ratchet_trail_pct(100.0, 110.0, 0) == 1.5,
      "at +2% peak the width ratchets to 1.5 and stays there")
check("4.5", bs._ratchet_trail_pct(100.0, 99.0, 0) == 3.0
      and bs._ratchet_trail_pct(0.0, 110.0, 0) == 3.0,
      "sub-entry highs and dead entries fall back to the base width")

print("\n".join(lines))
print(f"RESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
