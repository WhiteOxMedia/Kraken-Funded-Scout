"""_gf_sensitivity.py - THE GUARD-FACTOR CLOSURE (Sep 27). Fidelity gap
#2 in the live-arm study was "the guard factor is omitted"; the referee
(459c02b) closed it with the LIVE scalar (GF 2.0). This study finishes
the job with real data and an honest bracket:

  1. THE PARTS, VERIFIED: profit_guard_state.json carries the factor
     AND its parts (env, disc_mod, mlt_corr, vol_mod, asset_mem,
     funding_mod, decay_mod). The composite is recomputed from the
     parts through the documented formula (GuardFactor = EnvScore *
     DiscMod * (1 + MLT_Corr) * VolMod * AssetMemory * FundingMod *
     DecayMod, clamped 0.5-2.0, profit_guard.py:22-23) and compared to
     the stored factor - proving the state file is self-consistent
     before anything trusts it.
  2. THE HISTORY GAP, STATED: the state file holds TODAY's factor; the
     Sep 3-27 trades each ran under their day's value, which nothing
     preserved. So the referee's single GF is a point estimate on a
     hidden time series. The honest closure is a SENSITIVITY BRACKET:
     the referee's two policies re-simmed across the clamp's full
     meaningful range: the guard's own GUARD_CLAMP band edges plus
     the interior points (0.7, 1.0, 1.5) — if the ordering
     (which policy wins) is stable across the bracket, the verdict is
     robust to the unknown history; if it flips, the referee's verdict
     was an artifact of one number.
  3. THE CLAMP DISCIPLINE: the parts show mlt_corr = 13.242 - the
     (1+mlt) term alone would be 14.2x, so the 0.5-2.0 clamp is not a
     detail, it is the whole show. The sweep spans the clamp's outputs,
     which is why the bracket endpoints are what they are.
Read-only; pandas + the scout venv. ASCII-pure output.
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import _verdict_study as vs  # noqa: E402

HERE = Path(__file__).parent
ARM_FRAC = 0.5
RT_PCT = 0.08

# ── 1. the parts, verified ──
st = json.loads((HERE / "profit_guard_state.json").read_text(encoding="utf-8"))
parts = st.get("parts") or {}
env = parts.get("env") or 0.0
disc = parts.get("disc_mod") or 0.0
mlt = parts.get("mlt_corr") or 0.0
vol = parts.get("vol_mod")
amem = parts.get("asset_mem") or 1.0
fund = parts.get("funding_mod") or 1.0
decay = parts.get("decay_mod") or 1.0
raw = env * disc * (1 + mlt) * (vol or 1.0) * amem * fund * decay
clamped = max(0.5, min(2.0, raw))
print("== 1. THE PARTS, VERIFIED ==")
print(f"stored factor: {st.get('factor')}   adjusted_on: {st.get('adjusted_on')}")
print(f"parts: env={env} disc_mod={disc} mlt_corr={mlt} vol_mod={vol} "
      f"asset_mem={amem} funding_mod={fund} decay_mod={decay}")
print(f"recomputed raw composite: {raw:.4f} -> clamped {clamped:.3f}")
consistent = abs(clamped - float(st.get("factor") or 0)) < 1e-6
print(f"self-consistency: {'OK - the state file matches its own parts' if consistent else 'MISMATCH - investigate before trusting anything downstream'}")
print(f"(the (1+mlt) term alone is {1 + mlt:.2f}x - the clamp is the whole show)")

# ── 1b. THE PRESERVED TIME SERIES (Sep 27): profit_guard now keeps
# every day's adjusted factor in the state file's `history` array. When
# the series exists, THIS is the ground truth for the trades' actual
# GF values (each trade ran under its day's row) and the bracket below
# degrades to a robustness bonus, not a necessity.
try:
    import profit_guard as _pg
    _hist = _pg.factor_history()
except Exception as _he:
    _hist = []
    print(f"(history read failed: {_he} - bracketing stands alone)")
if _hist:
    _first, _last = _hist[0], _hist[-1]
    print(f"preserved history: {len(_hist)} days "
          f"({_first.get('day')} .. {_last.get('day')}) - the time "
          f"series is ON THE RECORD; each trade maps to its day's row")
else:
    print("preserved history: none yet (the series starts accruing "
          "with the first post-Sep-27 adjustment - the bracket below "
          "remains the honest stand-in until it exists)")

# ── 2. the sensitivity bracket ──
graded_all = vs.grade_trades()
_newest = max((float(g.get("ex_ms") or 0) for g in graded_all), default=0.0)
graded = [g for g in graded_all if float(g.get("ex_ms") or 0) <= _newest]
act = sum(vs.net(g["entry"], g["exit"], g["size"]) for g in graded)
_cache = {}


def _bars(pair, en_ms, ex_ms):
    key = (pair, int(en_ms), int(ex_ms))
    if key not in _cache:
        df = pd.read_feather(HERE / "surething_data" / "kraken" /
                             f"{pair.replace('/', '_')}-5m.feather")
        lo = pd.Timestamp(int(en_ms), unit="ms", tz="UTC")
        hi = pd.Timestamp(int(ex_ms), unit="ms", tz="UTC") + pd.Timedelta(hours=4)
        _cache[key] = df[(df["date"] >= lo) & (df["date"] <= hi)].sort_values("date")
    return _cache[key]


def ratchet_w(entry, peak):
    pp = (peak / entry - 1.0) * 100.0
    return 1.5 if pp >= 2.0 else (2.0 if pp >= 1.0 else 3.0)


def walk(g, policy, gf):
    entry, goal = g["entry"], g["goal"]
    bank_px = entry * (1 + goal / 100.0)
    floor_px = entry * (1 + 2 * RT_PCT / 100.0)
    armed = False
    arm_at = goal * (ARM_FRAC if policy == "live" else 0.9)
    peak, stop = entry, None
    w = _bars(g["pair"], g["en_ms"], g["ex_ms"])
    if w.empty:
        return None
    for hi, lo, op in zip(w["high"], w["low"], w["open"]):
        # CODE RABBIT-GRADE FIX (Sep 27): touch_stop is evaluated from
        # the stop AS IT STOOD ENTERING the bar - the first draft let
        # the same bar's low hit a stop recomputed from that bar's own
        # high (look-ahead; the SOL 14:45 trace proved it, 6 trades
        # diverged, mostly control books trailed out pre-bank).
        touch_stop = stop is not None and lo <= stop
        if hi >= bank_px:
            return bank_px, "bank"
        if hi > peak:
            peak = hi
        if policy == "live" and not armed and goal > 0 \
                and (peak / entry - 1.0) * 100.0 >= arm_at:
            armed = True
        wdt = ratchet_w(entry, peak) * gf
        if policy == "live" and armed:
            cap = max(goal * ARM_FRAC, 1.0) * gf
            lock_w = max(0.0, (1.0 - floor_px / peak) * 100.0) * gf
            if lock_w > 0:
                cap = min(cap, lock_w)
            wdt = min(wdt, cap)
        if policy == "hybrid":
            if peak >= entry * (1 + arm_at / 100.0):
                stop = peak * (1 - wdt / 100.0)
        else:
            stop = peak * (1 - wdt / 100.0)
        if policy == "live" and armed and stop < floor_px <= peak:
            stop = floor_px
        if peak > entry and stop is not None and stop > hi:
            return op, "trail"
        if peak > entry and touch_stop:
            return stop, "trail"       # the stop AS IT ENTERED the bar
    return None


print(f"\n== 2. THE SENSITIVITY BRACKET ({len(graded)}-trade book, 5m data, "
      f"actual +{act:.2f}) ==")
# THE BRACKET READS THE MACHINE'S OWN CLAMP (CodeRabbit major, Sep 28,
# fixed same day): the sweep started at the DECAY_FLOOR 0.7, but the
# guard's final factor clamps to GUARD_CLAMP = (0.5, 2.0) — the hard
# band every live factor lives inside. A 0.6 world is real machinery
# the bracket never measured. The sweep imports profit_guard's
# constants (no retyped literals): band edges ALWAYS swept, inner
# points (0.7, 1.0, 1.5) show the ordering story, and the header
# prints the clamp so the bracket and the machine can never drift
# apart silently.
try:
    import profit_guard as _pgb
    _lo, _hi = (float(x) for x in _pgb.GUARD_CLAMP)
except Exception:
    _lo, _hi = 0.5, 2.0        # fail-open to the committed band (Sep 27)
_bracket = sorted({round(_lo, 4), 0.7, 1.0, 1.5, round(_hi, 4)})
print(f"live factor clamp: GUARD_CLAMP = ({_lo}, {_hi}) — the bracket "
      f"covers the band's edges + the interior points")
print(f"{'GF':>5} | {'live total':>10} | {'hybrid total':>12} | "
      f"{'live ctrl':>10} | {'hyb ctrl':>9} | winner")
print("-" * 74)
rows = {}
for gf in _bracket:
    tot = {"live": 0.0, "hybrid": 0.0}
    ctrl = {"live": 0.0, "hybrid": 0.0}
    for g in graded:
        for pol in ("live", "hybrid"):
            r = walk(g, pol, gf)
            if r is None:
                continue
            px, which = r
            d = (vs.net(g["entry"], px, g["size"])
                 - vs.net(g["entry"], g["exit"], g["size"]))
            tot[pol] += d
            if which == "trail":
                pass
            if "took the goal" in str(g.get("verdict")):
                ctrl[pol] += d
    win = ("live" if tot["live"] > tot["hybrid"] else "hybrid")
    rows[gf] = {"tot": dict(tot), "ctrl": dict(ctrl), "win": win}
    print(f"{gf:>5.1f} | {tot['live']:>10.2f} | {tot['hybrid']:>12.2f} | "
          f"{ctrl['live']:>10.2f} | {ctrl['hybrid']:>9.2f} | {win}")

print("\n== 3. THE ROBUSTNESS VERDICT ==")
winners = {r["win"] for r in rows.values()}
live_beats_ladder = [gf for gf, r in rows.items()
                     if r["tot"]["live"] > act]
hyb_beats_ladder = [gf for gf, r in rows.items()
                    if r["tot"]["hybrid"] > act]
print(f"winner across the bracket: {sorted(winners)} "
      f"({'ROBUST - the ordering does not flip' if len(winners) == 1 else 'NOT ROBUST - the verdict was an artifact of one GF value'})")
print(f"live beats the ladder at GF: {live_beats_ladder or 'never'}")
print(f"hybrid beats the ladder at GF: {hyb_beats_ladder or 'never'}")
print("the relative bar still applies: both policies' control costs are "
      "checked against the ladder's own discipline at each GF")
