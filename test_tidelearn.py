"""Test THE LEARNED TIDE (Sep 10) — the OUT gate learns from the deep
candle store instead of assuming "all ships sink together".

Graded here:
  1. the learner's math (quantiles, the [86, 97] clamp, thin evidence)
  2. fail-closed + rate-limited caching (a broken store retries once
     per TTL, not once per gate hit)
  3. the scanner's OUT gate order: LONE WOLF first, then the LEARNED
     BAR, then the JOURNAL RELEASE backstop — the bot ledger untouched
  4. the QC MIRRORS the alert path (same bar; it never spends the
     journal release)
  5. the REAL model over the REAL store (end-to-end, when present)

Run: .venv\\Scripts\\python.exe -X utf8 test_tidelearn.py
"""
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

results = []
def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))

def _src(name):
    return (BASE / name).read_text(encoding="utf-8").replace("\r\n", "\n")

bs_src = _src("background_scanner.py")
ap_src = _src("app.py")
tl_src = _src("tide_learn.py")

import tide_learn as tl
import scratch_space   # scratch policy: nothing volatile lands in the repo root

# ── 1. the math ──────────────────────────────────────────────────────
check("the clamp band is [86, 97]",
      tl.BAR_MIN == 86.0 and tl.BAR_MAX == 97.0)
check("the bar is a payer-rate quantile of the funnel's OWN scores",
      "1.0 - rate" in tl_src and "_funnel_score_quantiles" in tl_src)
check("quantiles interpolate linearly",
      abs(tl._quantile([10.0, 20.0], 0.5) - 15.0) < 1e-9
      and abs(tl._quantile([10.0, 20.0, 30.0], 0.0) - 10.0) < 1e-9
      and abs(tl._quantile([10.0, 20.0, 30.0], 1.0) - 30.0) < 1e-9)
check("quantile of a single point is that point",
      tl._quantile([42.0], 0.7) == 42.0)
check("empty scores -> None (fail-closed)",
      tl._quantile([], 0.5) is None)
check("a 14% payer tide demands the top 14% of scores",
      "_quantile(scores, max(0.0, min(1.0, 1.0 - rate)))" in tl_src)

# ── 1b. THE OUT-BAR OUTCOME REFIT (Sep 11) — the quantile is now the
# FALLBACK for OUT; the bar is fitted on live funnel outcomes when the
# evidence is thick (diag_tidebarrefit.py -> _tidebarrefit.txt)
check("the outcome-fit machinery exists",
      "def _outcome_fit_bar" in tl_src and "def _outcome_records" in tl_src
      and "def _pair_5m_arrs" in tl_src)
check("the outcome bar's floor is EVIDENCE, not the quantile clamp",
      tl.OUTCOME_BAR_FLOOR == 80.0 and tl.OUTCOME_BAR_FLOOR < tl.BAR_MIN)
check("the floor is EVIDENCE-GATED: an EV ladder may lower it below the "
      "old 80 clamp when the money says so",
      tl.OUTCOME_BAR_FLOOR_MIN < tl.OUTCOME_BAR_FLOOR
      and tl.OUTCOME_BAR_FLOOR_MIN > 0
      and "_ev_ladder_floor" in tl_src
      and "OUTCOME_BAR_FLOOR_MIN" in tl_src)
check("the personal bar floors at the source-matched floor, not the old 86 constant",
      "def tide_personal_bar" in bs_src
      and "_tl.state_bar_floor(tide_state)" in bs_src
      and "def state_bar_floor" in tl_src)
check("the outcome fit runs on BOTH candidate ledgers",
      "SUPPRESSIONS_FILE, SNAPSHOTS_FILE" in tl_src.replace("for path in (", ""))
check("the ship rule: EV >= the quantile's, or the quantile stays",
      'fits[st]["ev_o"] >= fits[st]["ev_q"]' in tl_src)

# ── 1c. THE PER-STATE EXTENSION (Sep 11) — the OUT refit machinery is
# now EVERY state's: each tide's bar ships on ITS OWN candidate evidence
check("the outcome fit is PER-STATE (records keep the tide, the fit filters on it)",
      '"state": tide' in tl_src and 'recs[recs["state"] == state]' in tl_src)
check("all three states fit (OUT/FLAT/RISING), one shared candle cache",
      'for _st in out' in tl_src and "arr_cache=_arr_cache" in tl_src
      and "def _outcome_fit_bar(state, scores, arr_cache=None)" in tl_src)
check("each state's fit joins >= the same evidence floor",
      tl.OUTCOME_MIN_JOIN == 40 and tl.OUTCOME_MIN_FIRES == 6)
check("tide_state_floor serves any state; the OUT alias is thin",
      "def tide_state_floor" in tl_src
      and tl.tide_out_floor.__doc__ is not None
      and "tide_state_floor" in (tl.tide_out_floor.__doc__ or "")
      and 'return tide_state_floor("OUT", max_age_s)' in tl_src)
check("tide_state_floor rejects an unknown state (fail-closed)",
      tl.tide_state_floor("SIDEWAYS")[0] is None
      and "unknown tide state" in tl.tide_state_floor("SIDEWAYS")[1])
check("the meta line names the state it prices",
      "learned {state} bar" in tl_src)

# ── 1d. THE AUTO-FLIP (Sep 11, tidebar_weekly.py): hindsight's veto —
# a flipped state ships the QUANTILE until the weekly report flips it back
import json as _json
_flip_dir = BASE / "_test_tideflip"
_flip_dir.mkdir(exist_ok=True)
_real_flipf = tl.FLIP_FILE
try:
    tl.FLIP_FILE = _flip_dir / "flips.json"   # GUARDED SWAP (testleaks, Sep 23)
    tl.FLIP_FILE.write_text(
        _json.dumps({"OUT": {"flipped": True}}), encoding="utf-8")
    _m = tl.learn()
    _o = (_m.get("states") or {}).get("OUT") or {}
    check("a flipped state ships the QUANTILE with the honest why",
          _o.get("bar_src") == "quantile"
          and "auto-flipped" in (_o.get("why") or ""), _o)
    tl.FLIP_FILE.write_text("{}", encoding="utf-8")
    _m2 = tl.learn()
    _o2 = (_m2.get("states") or {}).get("OUT") or {}
    check("an empty flip ledger restores the fit's own verdict",
          "auto-flipped" not in (_o2.get("why") or ""), _o2)
    tl.FLIP_FILE.unlink(missing_ok=True)
    _m3 = tl.learn()
    _o3 = (_m3.get("states") or {}).get("OUT") or {}
    check("a missing flip file fails OPEN (no flip why anywhere)",
          "auto-flipped" not in (_o3.get("why") or ""), _o3)
finally:
    tl.FLIP_FILE = _real_flipf
    import shutil as _sh
    _sh.rmtree(_flip_dir, ignore_errors=True)
check("the fallback is named honestly (thin AND lost)",
      "outcome fit thin" in tl_src and "outcome fit lost to the quantile" in tl_src)
check("provenance ships with the bar (bar_src + the fit summary)",
      '"bar_src": bar_src' in tl_src and '"outcome_fit"' in tl_src)
check("the meta line speaks the outcome fit when it ships",
      "outcome-fitted on" in tl_src)
check("thin evidence -> bar None with a why (the state stays blocked)",
      '"why": f"only {len(hrs)}h of history' in tl_src)

# ── 2. fail-closed + rate-limited caching ────────────────────────────
_real_learn = tl.learn
_calls = {"n": 0}

def _fake_learn_ok(tide_class=None, now=None):
    _calls["n"] += 1
    model = {"generated_ts": "t", "hours": 500, "window": ["a", "b"],
             "err": None,
             "states": {"OUT": {"payer_rate": 0.14, "hours": 257, "bar": 92.0,
                                "why": None},
                        "FLAT": {"payer_rate": 0.103, "hours": 248, "bar": 94.0,
                                 "why": None},
                        "RISING": {"payer_rate": 0.232, "hours": 219, "bar": 88.0,
                                   "why": None}}}
    tl._cache.update(data=model, ts=time.time(), err=None)
    return model

def _fake_learn_fail(tide_class=None, now=None):
    _calls["n"] += 1
    tl._cache.update(data=None, ts=time.time(), err="store broke")
    return {"err": "store broke", "states": {}, "generated_ts": None,
            "hours": 0, "window": None}

try:
    # cold cache + a working learn -> the bar comes back
    tl.learn = _fake_learn_ok
    tl._cache.update(data=None, ts=0.0, err=None)
    bar, meta = tl.tide_out_floor()
    check("cold cache: the learned OUT bar is served",
          bar == 92.0 and "92" in meta and "14.1" in meta.replace("14.0", "14.1")
          or (bar == 92.0 and "payers" in meta), meta)
    check("cold cache cost exactly ONE build", _calls["n"] == 1, _calls["n"])

    # warm cache: NO rebuild inside the TTL
    _calls["n"] = 0
    bar2, _ = tl.tide_out_floor()
    check("warm cache: no rebuild inside the TTL",
          bar2 == 92.0 and _calls["n"] == 0, (_calls["n"], bar2))

    # a FRESH failure is authoritative: no rebuild until the TTL expires
    tl.learn = _fake_learn_fail
    tl._cache.update(data=None, ts=time.time(), err="store broke")
    _calls["n"] = 0
    bar3, meta3 = tl.tide_out_floor()
    check("fresh failure: (None, why) served from the cache, no rebuild",
          bar3 is None and "store broke" in meta3 and _calls["n"] == 0,
          (bar3, meta3, _calls["n"]))

    # a STALE failure retries exactly once, then fails closed again
    tl._cache.update(data=None, ts=time.time() - tl.CACHE_TTL_S * 2,
                     err="store broke")
    bar4, meta4 = tl.tide_out_floor()
    check("stale failure: one retry, still fail-closed",
          bar4 is None and _calls["n"] == 1 and "store broke" in meta4,
          (bar4, _calls["n"], meta4))

    # thin evidence: the state stays blocked with its reason
    def _fake_learn_thin(tide_class=None, now=None):
        _calls["n"] += 1
        model = {"generated_ts": "t", "hours": 10, "window": ["a", "b"],
                 "err": None,
                 "states": {"OUT": {"payer_rate": None, "hours": 3, "bar": None,
                                    "why": "only 3h of history (need >= 48)"},
                            "FLAT": {}, "RISING": {}}}
        tl._cache.update(data=model, ts=time.time(), err=None)
        return model
    tl.learn = _fake_learn_thin
    tl._cache.update(data=None, ts=0.0, err=None)
    bar5, meta5 = tl.tide_out_floor()
    check("thin evidence: no bar, the gate keeps the blanket block",
          bar5 is None and "3h" in meta5, (bar5, meta5))
finally:
    tl.learn = _real_learn
    tl._cache.update(data=None, ts=0.0, err=None)

# ── 3. the scanner's gate: order, wiring, labels ─────────────────────
check("the learner bridge exists in the scanner (_tide_learn_bar)",
      "def _tide_learn_bar()" in bs_src
      and "import tide_learn as _tl" in bs_src
      and "_tl.tide_out_floor()" in bs_src)
check("the bridge fails closed on an import error",
      "the tide learner is unavailable" in bs_src)
lw_at = bs_src.find("if _lw_pts:")
learned_at = bs_src.find("_lbar, _lmeta = _tide_learn_bar()")
release_at = bs_src.find("_rel, _rel_why = _seam_releases_tide_out(")
check("gate ORDER: lone wolf first, learned bar second, journal backstop third",
      0 < lw_at < learned_at < release_at, (lw_at, learned_at, release_at))
check("the learned pass needs quick >= the coin's PERSONAL bar (Sep 10 rebalance)",
      "float(signal.confidence) >= _pbar" in bs_src)
check("the OUT gate prices the coin ONCE (the personal bar; no floor-side memory in OUT)",
      '_bs4.tide_personal_bar(pair, "OUT", _tl_bar)' in ap_src
      and 'if pair and tide_state != "OUT" and tide_state in tide_memory_states():' in bs_src)
check("the learned pass is logged (OUT-TIDE LEARNED PASS)",
      "OUT-TIDE LEARNED PASS" in bs_src)
check("the journal release stays as the backstop (OUT-TIDE JOURNAL RELEASE)",
      "OUT-TIDE JOURNAL RELEASE" in bs_src)
check("fail-closed text: no learned bar -> the blanket block is named",
      "no learned bar" in bs_src)
check("the suppression line shows the price (learned bar / no learned bar)",
      "the OUT tide's price" in bs_src)
check("the popup title marks the learned pass (not a lone wolf)",
      "— AGAINST THE TIDE" in bs_src)
check("the lone-wolf title is untouched",
      "— LONE WOLF (OUT TIDE)" in bs_src)
check("the OUT popup speaks the learned price",
      "it paid the learned price" in bs_src)
check("the bot ledger is untouched: OUT still writes NO bridge entry",
      "TIDE OUT (fleet %.0f%%) — no bridge write" in bs_src)
check("the learned flag is reset per candidate",
      "_out_tide_learned = False" in bs_src)

# ── 4. the QC mirrors the alert path (never disagree) ────────────────
check("the QC computes the learned pass and stores it",
      '"tide_learned_pass"' in ap_src and "_bs4._tide_learn_bar()" in ap_src)
check("the QC's hard gate admits the learned pass (and the lone wolf)",
      '((_tide_state != "OUT") or _lw_pts or _tl_pass)' in ap_src)
check("the QC's TIDE'S OUT rejection honors the learned pass",
      'not lw_pts and not tide_learned_pass' in ap_src)
check("the QC's toast honors the learned pass and names the price",
      "not _lw_pts and not _tl_pass" in ap_src
      and "learned bar" in ap_src)
check("the QC's OUT LED turns amber on the learned pass",
      '"a" if (tide_state == "OUT" and (lw_pts or tide_learned_pass))' in ap_src)
check("the QC shows a TIDE BAR PAID LED when the bar is paid",
      "TIDE BAR PAID" in ap_src)
check("the QC never spends the journal release (alert-side only)",
      "_seam_releases_tide_out" not in ap_src)

# ── 5. the REAL model over the REAL store (end-to-end) ───────────────
_store = BASE / "surething_data" / "kraken"
if not _store.exists() or not list(_store.glob("*.feather")):
    check("real learn skipped (no deep store in this environment)", True,
          "store absent")
else:
    _t0 = time.time()
    model = tl.learn()
    _dt = time.time() - _t0
    if model.get("err"):
        check("real learn: fail-closed shape on a broken store",
              model.get("states") == {} and tl.tide_out_floor()[0] is None,
              model.get("err"))
    else:
        outs = model.get("states") or {}
        check("real learn covers all three states",
              set(outs) == {"OUT", "FLAT", "RISING"}, list(outs))
        _out = outs.get("OUT") or {}
        if _out.get("bar") is None:
            check("real learn: OUT unpriced -> the gate stays closed",
                  tl.tide_out_floor()[0] is None, _out.get("why"))
        else:
            _lo = min(tl.BAR_MIN, tl.OUTCOME_BAR_FLOOR)
            check("real learn: the OUT bar is inside its band "
                  "([86, 97] quantile / [80, 97] outcome-fitted)",
                  _lo <= _out["bar"] <= tl.BAR_MAX, _out)
            check("real learn: the bar's provenance is on the record",
                  _out.get("bar_src") in ("outcomes", "quantile"),
                  _out.get("bar_src"))
            if _out.get("bar_src") == "outcomes":
                _fit = _out.get("outcome_fit") or {}
                check("real learn: the shipped outcome fit clears its floors",
                      _fit.get("n", 0) >= tl.OUTCOME_MIN_JOIN
                      and _fit.get("ev_o", -1) >= _fit.get("ev_q", 0), _fit)
            check("real learn: the payer rate is a sane fraction",
                  0.0 <= (_out.get("payer_rate") or -1) <= 1.0,
                  _out.get("payer_rate"))
            check("real learn: the model reflects the fleet study's shape "
                  "(OUT pays, ~14% of coin-hours)",
                  0.05 <= (_out.get("payer_rate") or 0) <= 0.40,
                  _out.get("payer_rate"))
        _bar, _meta = tl.tide_out_floor()
        check("real learn: tide_out_floor agrees with the model",
              (_bar is None) == (_out.get("bar") is None), (_bar, _meta))
        check("real learn: one full build in seconds (not minutes)",
              _dt < 60.0, f"{_dt:.1f}s")

# ── RUN ──────────────────────────────────────────────────────────────
fails = [r for r in results if not r[1]]
(scratch_space.report_temp('_tidelearn_check.txt')).write_text(
    "\n".join(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{d}]" if d and not ok else "")
              for name, ok, d in results)
    + f"\n\nRESULT: {len(results) - len(fails)} passed, {len(fails)} failed\n",
    encoding="utf-8")
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
