"""test_tidebarweekly.py — the weekly outcome-vs-quantile hindsight.

Pins (all offline; the production report and the learner cache are
sandboxed so no test ever touches them):
  1. the driver wiring (period, first delay, the scanner block)
  2. the grading math — _grade() prices fires with the house convention
     and never fires below the bar
  3. the counterfactual quantile is computed exactly as learn() would
  4. a full pass writes the section with both bars and the delta, and
     a thin week / broken store fail-safe keeps the old report
  5. the report prepends (newest first) and rotates at ~500 KB
Run: .venv\\Scripts\\python.exe -X utf8 test_tidebarweekly.py
"""
import sys
import tempfile
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import tidebar_weekly as tbw
import scratch_space   # scratch policy: nothing volatile lands in the repo root

results = []
def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))

# ── 1. wiring ────────────────────────────────────────────────────────
check("the reckoning tally rides the shared census (the chip and the "
      "trail line both append it, both surfaces one format)",
      "verdict_census(_counts_chip)" in
      (BASE / "app.py").read_text(encoding="utf-8")
      and "reckoning_tally(_evs_all)" in
      (BASE / "app.py").read_text(encoding="utf-8")
      and "reckoning_tally(evs)" in
      (BASE / "app.py").read_text(encoding="utf-8"))
check("the tally counts only REAL stamped flip-backs (sims never "
      "count; a malformed stamp is skipped, not fatal)",
      tbw.reckoning_tally([
          {"sim": True, "action": "flip_back",
           "borrow_reckoning": {"status": "agree"}},
          {"action": "flip_back", "borrow_reckoning": {"status": "agree"}},
          {"action": "flip_back", "borrow_reckoning": {"status": "disagree"}},
          {"action": "flip_back", "borrow_reckoning": "junk"},
          {"action": "flip_back"},
          {"action": "flip", "borrow_reckoning": {"status": "agree"}},
      ]) == " · 1 agreed · 1 disagreed")
check("nothing graded -> the tally is '' (no fake 0 · 0)",
      tbw.reckoning_tally([]) == ""
      and tbw.reckoning_tally(None) == ""
      and tbw.reckoning_tally(
          [{"sim": True, "action": "flip_back",
            "borrow_reckoning": {"status": "agree"}}]) == "")

# ── 1. wiring ────────────────────────────────────────────────────────
check("the pass runs DAILY with a shortly-after-boot first pass",
      abs(tbw.PERIOD_S - 86400) < 1 and 0 < tbw.FIRST_DELAY_S <= 3600)
check("the GRADE window stays the trailing 7 days",
      tbw.WINDOW_DAYS == 7)
check("the flip ledger stays week-keyed (daily runs dedupe on the ISO week)",
      tbw.FLIP_WEEKS == 3 and "week_tag" in
      (BASE / "tidebar_weekly.py").read_text(encoding="utf-8"))
bs_src = (BASE / "background_scanner.py").read_text(encoding="utf-8")
check("the scanner wires the weekly driver beside its siblings",
      "tidebar_weekly" in bs_src and "tidebar-weekly" in bs_src
      and "weekly_driver_loop" in bs_src)
check("the report prepends and rotates (the family convention)",
      tbw.REPORT_MAX_BYTES == 500_000
      and "blob_new + prev" in (BASE / "tidebar_weekly.py").read_text(encoding="utf-8"))
check("the ledgers the report reads are written atomically (no "
      "empty-read race; the rename rides the retry contract — a "
      "concurrent reader cannot lose a writer its row)",
      "replace_atomic(_tmp, SUPPRESSION_LOG_FILE)" in bs_src
      and "replace_atomic(_tmp, SIGNAL_SNAPSHOTS_FILE)" in bs_src)
check("the flip grade runs MONTHLY beside its driver siblings",
      "flipgrade_driver_loop" in bs_src
      and abs(tbw.FLIPGRADE_PERIOD_S - 30 * 86400) < 1
      and 0 < tbw.FLIPGRADE_FIRST_DELAY_S <= 3600
      and tbw.FLIPGRADE_MIN_WEEKS >= 2)
check("the manual seed-replay cannot clobber the driver's grade history",
      "_flipgrade_sim.txt" in (BASE / "diag_flipgrade.py")
      .read_text(encoding="utf-8") and tbw.FLIPGRADE_FILE.name == "_flipgrade.txt")

# ── THE WAKE (Sep 11): the flip grade re-runs shortly after each
# weekly tide-bar pass lands (report_now sets _grade_wake), so the
# aftermath dollars stay fresh between monthly runs — and a wake on an
# unchanged ledger is a no-op, never a duplicate section ──────────────
import shutil as _shl
_wake_sbx = BASE / "_test_wake_pin_dir"
_shl.rmtree(_wake_sbx, ignore_errors=True)
_wake_sbx.mkdir()
_real_wk = {k: getattr(tbw, k) for k in
            ("HISTORY_FILE", "FLIPGRADE_FILE", "FLIP_EVENTS_FILE",
             "FLIP_FILE", "FLIPGRADE_JSON", "POOLMIN_LEARN_FILE",
             "RECKONING_WINDOW_FILE")}
try:
    tbw.HISTORY_FILE = _wake_sbx / "h.jsonl"
    tbw.FLIPGRADE_FILE = _wake_sbx / "fg.txt"
    tbw.FLIP_EVENTS_FILE = _wake_sbx / "ev.jsonl"
    tbw.FLIP_FILE = _wake_sbx / "flips.json"
    tbw.FLIPGRADE_JSON = _wake_sbx / "fg.json"
    tbw.POOLMIN_LEARN_FILE = _wake_sbx / "pm.json"
    tbw.RECKONING_WINDOW_FILE = _wake_sbx / "rkwin.jsonl"
    import json as _json
    # the PRODUCTION ledger grows as real weeks land — pick the W37 row
    # by week tag, never assume the file is one line long
    # clock-audit: ok — LIVE-PROBE (the week picker is tagged, so the
    # read is stable under ledger growth; the row is copied into the
    # sandbox below) (hermeticity sweep)
    _w37 = next(r for r in (_json.loads(l) for l in
                            (BASE / "_tidebarweekly_history.jsonl")
                            .read_text(encoding="utf-8").splitlines()
                            if l.strip())
                if r.get("week") == "2026-W37")
    tbw.HISTORY_FILE.write_text(_json.dumps(_w37) + "\n", encoding="utf-8")
    _stop = threading.Event()
    _wake = threading.Event()
    _t = threading.Thread(
        target=tbw.flipgrade_driver_loop, args=(_stop,),
        kwargs={"wake": _wake, "settle_s": 0.2, "first_delay_s": 0.3,
                "period_s": 3600.0}, daemon=True)
    _t.start()
    time.sleep(1.2)
    check("the woken flip grade still HOLDS below the minimum history "
          "(nothing written, the hold reason logged)",
          not tbw.FLIPGRADE_FILE.exists())
    tbw.HISTORY_FILE.write_text(
        _json.dumps(_w37) + "\n"
        + _json.dumps(dict(_w37, week="2026-W38")) + "\n",
        encoding="utf-8")
    _wake.set()   # simulate the weekly pass landing on the new week
    time.sleep(2.0)   # settle 0.2 + the grade
    _txt = tbw.FLIPGRADE_FILE.read_text(encoding="utf-8")
    check("a wake on a CHANGED ledger grades immediately (W37..W38 "
          "section written by the woken run, not the monthly clock)",
          _txt.count("=== THE FLIP GRADE") == 1
          and "2 weeks: 2026-W37..2026-W38" in _txt,
          _txt[:300])
    tbw.HISTORY_FILE.write_text(
        "\n".join(_json.dumps(dict(r, generated_ts="2026-09-15 01:45 UTC"))
                  for r in (_json.loads(l) for l in
                            tbw.HISTORY_FILE.read_text(
                                encoding="utf-8").splitlines() if l.strip()))
        + "\n", encoding="utf-8")
    _wake.set()   # a same-week refresh: timestamps only
    time.sleep(2.0)
    check("a wake on an UNCHANGED ledger is a no-op (timestamps don't "
          "count — no duplicate section)",
          tbw.FLIPGRADE_FILE.read_text(encoding="utf-8")
          .count("=== THE FLIP GRADE") == 1)
    _stop.set()
    _t.join(timeout=5)
finally:
    for _k, _v in _real_wk.items():
        setattr(tbw, _k, _v)
    _shl.rmtree(_wake_sbx, ignore_errors=True)

# ── THE OPEN-BENCH SIDECAR (Sep 12): grade_ledger writes the live
# verdict for currently-benched states to _flipgrade.json on EVERY
# path — success grades it, a hold CLEARS it — so the FLIP GRADE
# plate shows the bench's running P&L without the audit expander ───
_ob_sbx = BASE / "_test_openbench_dir"
_shl.rmtree(_ob_sbx, ignore_errors=True)
_ob_sbx.mkdir()
_real_ob = {k: getattr(tbw, k) for k in
            ("HISTORY_FILE", "FLIP_FILE", "FLIPGRADE_FILE",
             "FLIPGRADE_JSON", "FLIP_EVENTS_FILE", "POOLMIN_LEARN_FILE",
             "RECKONING_WINDOW_FILE")}
try:
    for _k, _v in _real_ob.items():
        setattr(tbw, _k, _ob_sbx / _v.name)

    def _ob_rec(wk, ff, fq):
        return {"week": wk, "states": {"OUT": {
            "in_force": {"fires": ff[0], "payers": 1,
                         "usd": ff[0] * ff[1], "usd_per_fire": ff[1]},
            "other": {"fires": fq[0], "payers": 1,
                      "usd": fq[0] * fq[1], "usd_per_fire": fq[1]},
            "other_src": "quantile"}}}

    # a lived bench: the pass benched OUT at W43; W44-45 graded since
    tbw.HISTORY_FILE.write_text("".join(
        _json.dumps(r) + "\n" for r in
        [_ob_rec("2026-W40", (10, 2.0), (10, 1.0)),
         _ob_rec("2026-W41", (10, -1.0), (10, 1.0)),
         _ob_rec("2026-W42", (10, -1.0), (10, 1.0)),
         _ob_rec("2026-W43", (10, -1.0), (10, 1.0)),
         _ob_rec("2026-W44", (10, -1.0), (10, 1.0)),
         _ob_rec("2026-W45", (10, -1.0), (10, 1.0))]),
        encoding="utf-8")
    tbw.FLIP_FILE.write_text(_json.dumps(
        {"OUT": {"flipped": True, "since": "2026-09-10"},
         "FLAT": {"flipped": False}, "RISING": {"flipped": False}}),
        encoding="utf-8")
    tbw.FLIP_EVENTS_FILE.write_text(_json.dumps(
        {"state": "OUT", "action": "flip", "week": "2026-W43",
         "ts": "2026-09-10", "losing_weeks": 3, "roll_delta": -1.10,
         "evidence": [{"week": "2026-W41", "fit_edge": -2.0},
                      {"week": "2026-W42", "fit_edge": -2.0},
                      {"week": "2026-W43", "fit_edge": -2.0}]}) + "\n",
        encoding="utf-8")
    tbw.grade_ledger()
    _p = _json.loads(tbw.FLIPGRADE_JSON.read_text(encoding="utf-8"))
    _ob = _p.get("open_bench") or []
    check("the completed grade writes the open-bench sidecar with the "
          "benched state's live verdict",
          len(_ob) == 1 and _ob[0]["state"] == "OUT"
          and _ob[0]["verdict"] == "COSTING"
          and _ob[0]["verdict_col"] == "#ff6b5e"
          and "quantile realized $-1.00/fire" in _ob[0]["text"]
          and "the fit's $+1.00/fire" in _ob[0]["text"], _ob)
    check("the sidecar carries the bench's CUMULATIVE cost-to-date "
          "(realized $-20 minus the counterfactual's $+20 = $-40)",
          _ob[0].get("to_date") == -40.0
          and "to date the bench is down $40.00 cumulative"
          in _ob[0]["text"], _ob)
    check("the open-bench verdict rides the shared _event_aftermath "
          "math (open window, next_ev=None — one math everywhere)",
          "next_ev=None" in (BASE / "tidebar_weekly.py")
          .read_text(encoding="utf-8"))
    check("the completed grade's sidecar carries the pool-gate dial "
          "blocks for the plate (family, dial grade, arc-3 default) — "
          "the monthly verdict travels without the txt",
          "poolgate" in _p and "poolgate_family" in _p
          and "poolmin_default" in _p
          and isinstance((_p.get("poolmin_default") or {})
                         .get("n_priced"), int),
          {k: _p.get(k) for k in
           ("poolgate", "poolgate_family", "poolmin_default")})
    check("the completed grade's sidecar carries the STREAK dial's "
          "graduated evidence (FBW candidates' denied/delayed/net) so "
          "both dials' verdicts render beside each other on MASTER",
          set((_p.get("poolgate_streak") or {}))
          == {str(fw) for fw in tbw.FBWGATE_WEEKS}
          and all(isinstance(v.get("net"), (int, float))
                  and isinstance(v.get("denied"), int)
                  and isinstance(v.get("delayed"), int)
                  for v in (_p.get("poolgate_streak") or {}).values()),
          _p.get("poolgate_streak"))
    check("the completed grade's sidecar carries the LEARNED BARS "
          "block: the gate's live map and the fresh learner verdict "
          "(per state: bar, edge-week count, median, reason) so the "
          "plate shows what gates and what's about to",
          isinstance((_p.get("poolmin_learned") or {}).get("map"), dict)
          and set(((_p.get("poolmin_learned") or {}).get("learner"))
                  or {}) == {"OUT", "FLAT", "RISING"}
          and all(isinstance(v, dict)
                  and set(v) == {"pool_min", "n_edges", "median",
                                 "reason"}
                  for v in (_p.get("poolmin_learned") or {})
                  .get("learner", {}).values()),
          _p.get("poolmin_learned"))
    check("the completed grade's txt renders the LEARNED BARS block: "
          "each state's truth named apart (IN FORCE / DERIVED THIS "
          "GRADE / global dial with its thin reason) — the evidence "
          "reads without opening the learner's file",
          "THE LEARNED BARS" in tbw.FLIPGRADE_FILE.read_text(
              encoding="utf-8")
          and all(f"  {s}: global dial" in tbw.FLIPGRADE_FILE
                  .read_text(encoding="utf-8")
                  for s in ("OUT", "FLAT", "RISING")),
          [l for l in tbw.FLIPGRADE_FILE.read_text(encoding="utf-8")
           .splitlines() if l.strip().startswith(
               ("OUT:", "FLAT:", "RISING:"))][:4])

    # the hold path rewrites the sidecar — stale dollars are cleared
    # (verdict None), and the bench itself stays honestly visible as
    # pending because the trail still says OUT is benched
    tbw.HISTORY_FILE.write_text(
        _json.dumps(_ob_rec("2026-W37", (10, 1.0), (10, 1.0))) + "\n",
        encoding="utf-8")
    tbw.FLIPGRADE_JSON.write_text('{"stale": true}', encoding="utf-8")
    _s = tbw.grade_ledger()
    _p2 = _json.loads(tbw.FLIPGRADE_JSON.read_text(encoding="utf-8"))
    _ob2 = _p2.get("open_bench") or []
    check("a held grade rewrites the sidecar — stale dollars cleared, "
          "the bench honestly pending (never hidden, never fake)",
          bool(_s.get("held")) and len(_ob2) == 1
          and _ob2[0]["state"] == "OUT" and _ob2[0]["verdict"] is None
          and _ob2[0]["to_date"] is None
          and "pending" in _ob2[0]["text"]
          and "stale" not in _p2
          and "generated_ts" in _p2, _p2)
    check("a held grade CLEARS the pool-gate dial blocks too — a stale "
          "dial verdict never outlives its grade (the plate stays "
          "honest on a hold)",
          "poolgate" not in _p2 and "poolgate_family" not in _p2
          and "poolmin_default" not in _p2
          and "poolmin_learned" not in _p2
          and "poolgate_streak" not in _p2, sorted(_p2))
finally:
    for _k, _v in _real_ob.items():
        setattr(tbw, _k, _v)
    _shl.rmtree(_ob_sbx, ignore_errors=True)

# ── THE REGIME WATCH WARN (Sep 11): the scanner's side of the watch —
# one WARN the first time a state slides toward a bench, quiet while it
# stays pressured, re-armed when the pressure lifts (recovery or a
# flip-back climb), and the WARN names the NEWLY pressured state ───────
import logging as _logging

class _Cap(_logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, r):
        self.records.append(r)


_cap = _Cap()
tbw.logger.addHandler(_cap)
try:
    def _mk(out_e, flat_e=(0.5, 0.6, 0.7)):
        return {"OUT": list(out_e), "FLAT": list(flat_e),
                "RISING": [0.5, 0.6, 0.7]}

    _flips = {nm: {"flipped": False} for nm in ("OUT", "FLAT", "RISING")}
    _PRES = (-0.5, -0.8, -1.2)     # falling, 3 losing weeks
    _REC = (-0.9, 0.2, 0.8)        # rising slope, live → not pressured

    def _warn_names():
        _txts = [r.getMessage() for r in _cap.records
                 if r.levelno >= _logging.WARNING]
        return (_txts,
                next((nm for nm in ("OUT", "FLAT", "RISING")
                      if any(nm in t for t in _txts)), None))

    tbw._regime_warned.clear()
    _cap.records.clear()
    _ret = tbw.warn_on_bench_pressure(_mk(_PRES), _flips)
    _txts, _named = _warn_names()
    check("the first pressure WARNs on the state and returns its phrase",
          _named == "OUT" and len(_txts) == 1 and "OUT" in str(_ret)
          and "losing" in _txts[0], _txts)

    _cap.records.clear()
    _ret = tbw.warn_on_bench_pressure(_mk(_PRES), _flips)
    _txts, _named = _warn_names()
    check("repeat pressure stays QUIET but still returns the phrase",
          _named is None and len(_txts) == 0 and "OUT" in str(_ret),
          _txts)

    _cap.records.clear()
    _ret = tbw.warn_on_bench_pressure(_mk(_REC), _flips)
    _txts, _named = _warn_names()
    check("recovery LIFTS the episode: no warn, None, the state re-armed",
          _named is None and _ret is None
          and "OUT" not in tbw._regime_warned, _txts)

    _cap.records.clear()
    _ret = tbw.warn_on_bench_pressure(_mk(_PRES), _flips)
    _txts, _named = _warn_names()
    check("a re-slide after the lift WARNs fresh",
          _named == "OUT" and len(_txts) == 1, _txts)

    _cap.records.clear()
    _ret = tbw.warn_on_bench_pressure(_mk(_PRES, (-0.4, -0.6, -0.9)),
                                      _flips)
    _txts, _named = _warn_names()
    check("the WARN names the NEWLY pressured state (FLAT), not the "
          "already-warned OUT", _named == "FLAT" and len(_txts) == 1
          and "OUT" in str(_ret), _txts)

    tbw.warn_on_bench_pressure(_mk(_REC), _flips)   # everything lifts
    check("the warned ledger is clean once every pressure lifts",
          not tbw._regime_warned, sorted(tbw._regime_warned))

    tbw._regime_warned.clear()
    tbw._regime_warned.add("FLAT")
    tbw.warn_on_bench_pressure(
        _mk(_PRES), {**_flips, "FLAT": {"flipped": True}})
    check("a benched state CLIMBING toward its flip-back lifts its "
          "episode (re-armed for a later re-bench)",
          "FLAT" not in tbw._regime_warned, sorted(tbw._regime_warned))
finally:
    tbw.logger.removeHandler(_cap)
    tbw._regime_warned.clear()

# ── THE WATCHER'S OWN GRADE (Sep 12): the regime watch's forecast
# judged against the gate's actual decisions over the same ledger.
# The proxy measures the pooled series' CHANGE, the gate measures the
# per-week LEVEL — the grade counts agree / early / late. Pure: the
# walk replays the ledger's own deterministic gate from an un-flipped
# seed, so it reads only the records — no events file, no flips file,
# and the trail's sims can never join it ─────────────────────────────
def _grec(wk, ff, fq):
    return {"week": wk, "states": {"OUT": {
        "in_force": {"fires": ff[0], "payers": 1,
                     "usd": ff[0] * ff[1], "usd_per_fire": ff[1]},
        "other": {"fires": fq[0], "payers": 1,
                  "usd": fq[0] * fq[1], "usd_per_fire": fq[1]},
        "other_src": "quantile"}}}

# the steady bleed: pool and per-week edges sink together — the
# watcher names 'gate due now' exactly at the firing week (AGREE)
_gB = [_grec(f"W{i}", (10, 2.0 if i == 1 else -1.0), (10, 1.0))
       for i in range(1, 8)]
_gB_out = tbw.grade_regime_watch(_gB)["OUT"]
check("the watcher's forecast AGREES with the gate on a steady bleed "
      "(due-now exactly at the flip week)",
      _gB_out["weeks_checked"] == 6 and _gB_out["agree"] == 1
      and _gB_out["gate_fired_proxy_not_due"] == 0
      and _gB_out["proxy_due_gate_quiet"] == 0, _gB_out)

# the fat-window divergence: a fat W1 fit win keeps the POOL positive
# while the per-week edges bleed — the gate fires while the watcher
# still forecasts weeks to go (LATE: the exact finding the grade
# exists to catch)
_gA = [_grec(f"W{i}", (10, -1.0), (10, 1.0)) for i in range(2, 8)]
_gA.insert(0, _grec("W1", (30, 3.0), (10, 1.0)))
_gA_out = tbw.grade_regime_watch(_gA)["OUT"]
check("the watcher is LATE when a fat pooled window blinds it (the "
      "gate fires while the proxy still forecasts weeks)",
      _gA_out["gate_fired_proxy_not_due"] == 1 and _gA_out["agree"] == 0,
      _gA_out)

# the pre-decision fair-play rule: report_now evaluates the flips
# FIRST, then warns — so the fired week is graded on the PRE-decision
# phrase ('gate due now'), never the already-switched post phrase
_gB_fired = [f for f in _gB_out["forecasts"] if f["gate_fired"]]
check("the fired week is graded on the PRE-decision phrase (eta 0, "
      "'gate due now' — not the post-flip silence)",
      len(_gB_fired) == 1 and _gB_fired[0]["eta"] == 0
      and "gate due now" in _gB_fired[0]["phrase"], _gB_fired)

# a thin ledger holds: fewer than 2 graded points checks nothing
check("a thin ledger holds the watcher grade (checked 0 below two "
      "graded points)",
      tbw.grade_regime_watch(_gB[:1])["OUT"]["weeks_checked"] == 0)

# the txt header names the blindness (change vs level) so a late count
# is never read as the watcher lying
check("the flip grade's watcher section names the change-vs-level "
      "blindness",
      "measures CHANGE" in
      (BASE / "tidebar_weekly.py").read_text(encoding="utf-8"))

# ── sandbox: the production report + the learner cache + the fill ledgers
real_out = tbw.OUT_FILE
# the production report's byte snapshot: the final guard checks the
# battery never touched production — format-independently (an anchor
# prose string goes stale whenever the writer's format evolves)
# THE LIVE-WRITER RACE (Oct 4 battery, dead 8.7s in): the app's weekly
# plate writer replaces this file while the suite runs — an exists()
# probe can still lose the window and the read dies with an unhandled
# FileNotFoundError. Retry once (the writer's window is microscopic),
# then grade on what parsed (None): the byte-stable guard compares
# like with like either way.
def _out_bytes():
    for _attempt in (0, 1):
        try:
            return real_out.read_bytes() if real_out.exists() else None
        except FileNotFoundError:
            if _attempt == 0:
                time.sleep(0.5)
    return None


_prod_out_bytes = _out_bytes()
sandbox = BASE / "_test_tbw_dir"
# hermetic runs: a crashed earlier run (NameError, ^C mid-battery) must
# not leave its ledgers behind for this run to trip over
import shutil as _sh
_sh.rmtree(sandbox, ignore_errors=True)
sandbox.mkdir(exist_ok=True)
real_snap = tbw.SNAP_OUTCOMES_FILE
real_trades = tbw.TRADES_FILE
real_json = tbw.JSON_FILE
real_hist = tbw.HISTORY_FILE
real_flip = tbw.FLIP_FILE
real_events = tbw.FLIP_EVENTS_FILE
real_poolmin = tbw.POOLMIN_LEARN_FILE
# the sandbox swaps live INSIDE the try below (the PULSE_JSON pattern):
# a crash before that finally must never leave the production paths
# pointing at _test_tbw_dir for the rest of the process
# shipped bars are production state — the suite proves its own behavior
# in its own world, never writes the real one
# THE COLD-BOOT TRANSIENT (Sep 10): the first pass after a restart once
# wrote an all-None trend "despite gradeable history" — the plate was
# reading a TEST-written sidecar: this suite ran report_now() against
# the PRODUCTION JSON_FILE. The sandbox line above IS the fix; this pin
# makes the fix a contract (the suite fails loudly if it regresses).
# fake fill ledgers: a FLAT fire that filled (+$5.50), a RISING fire
# whose only same-pair fill sold BEFORE the fire (must NOT join), and
# a suppressed record (ignored)
import json
import pandas as _pd
_T0 = (_pd.Timestamp.now(tz="UTC") - _pd.Timedelta(days=1))
snap_lines = [
    {"ts": _T0.isoformat(), "pair": "FLATX/USD", "kind": "fired",
     "tide": "FLAT 40%", "quick": 91.0, "spend": 250.0,
     "outcome": {"mfe_pct": 2.2, "speed": "fast", "payoff": "pop"}},
    {"ts": _T0.isoformat(), "pair": "RIX/USD", "kind": "fired",
     "tide": "RISING 78%", "quick": 88.0, "spend": 200.0,
     "outcome": {"mfe_pct": 2.0, "speed": "fast", "payoff": "pop"}},
    {"ts": _T0.isoformat(), "pair": "OTHX/USD", "kind": "suppressed",
     "tide": "OUT 30%", "quick": 70.0},
]
trade_lines = [
    {"sold_at": (_T0 + _pd.Timedelta(hours=2)).isoformat(),
     "pair": "FLATX/USD", "pnl_net": 5.5},
    {"sold_at": (_T0 - _pd.Timedelta(hours=1)).isoformat(),
     "pair": "RIX/USD", "pnl_net": 9.9},          # before the fire: no join
]
import tide_learn as tl
real_learn = tl.learn
real_cache = dict(tl._cache)
try:
    # the production paths re-pointed INSIDE the try (the PULSE_JSON
    # pattern): the finally restores them even on a mid-suite crash
    tbw.OUT_FILE = sandbox / "weekly.txt"
    tbw.SNAP_OUTCOMES_FILE = sandbox / "snap_outcomes.jsonl"
    tbw.TRADES_FILE = sandbox / "trades.jsonl"
    tbw.HISTORY_FILE = sandbox / "history.jsonl"
    tbw.FLIP_FILE = sandbox / "flips.json"
    tbw.JSON_FILE = sandbox / "sidecar.json"   # the production sidecar is
                                               # never clobbered by a test
    tbw.FLIP_EVENTS_FILE = sandbox / "flip_events.jsonl"
    tbw.POOLMIN_LEARN_FILE = sandbox / "poolmin.json"   # the learner's
    check("the suite sandboxes the production sidecar (the all-None "
          "cold-boot transient was test leakage into JSON_FILE)",
          tbw.JSON_FILE != real_json and tbw.JSON_FILE.name == "sidecar.json",
          (str(tbw.JSON_FILE), str(real_json)))
    # clock-audit: ok — SANDBOXED (tbw.SNAP_OUTCOMES_FILE redirected into the sandbox at :492) (clockbombs census 4)
    tbw.SNAP_OUTCOMES_FILE.write_text(
        "\n".join(json.dumps(r) for r in snap_lines) + "\n", encoding="utf-8")
    tbw.TRADES_FILE.write_text(
        "\n".join(json.dumps(r) for r in trade_lines) + "\n", encoding="utf-8")

    # a scripted model: OUT outcome-fitted, FLAT quantile, RISING none
    MODEL = {"err": None, "hours": 500, "generated_ts": "t",
             "window": ["a", "b"],
             "states": {"OUT": {"payer_rate": 0.109, "hours": 479,
                                "bar": 80.0, "bar_src": "outcomes",
                                "outcome_fit": {"n": 316}},
                        "FLAT": {"payer_rate": 0.105, "hours": 551,
                                 "bar": 92.0, "bar_src": "quantile"},
                        "RISING": {"payer_rate": 0.23, "hours": 153,
                                   "bar": None, "bar_src": "quantile",
                                   "why": "thin"}}}
    tl.learn = lambda *a, **k: MODEL

    # ── 1b. THE OPP VERDICT CADENCE (Oct 2, the accumulation window's
    #    scheduled read): scratch/opp_hindsight.py grades the opp class
    #    under rules PRE-REGISTERED before its first fire (<10 graded =
    #    KEEP-PROVISIONAL; EV<=0 or 0-payers-at-n>=20 = KILL; EV>0 but
    #    <50% of classic's = RETAIN-TIGHTEN; else KEEP). The verdict now
    #    prints ON SCHEDULE — subprocess-invoked (zero API coupling: the
    #    grader owns its method and ships its own bytes), parsed for the
    #    machine sidecar, prepended to the report. The tool is UNTRACKED
    #    scratch — the wiring never imports it and fail-safes when gone.
    check("1b.1 the verdict is read as a SUBPROCESS of the untracked "
          "grader (zero API coupling — the tool owns its method), the "
          "wiring never imports it",
          hasattr(tbw, "opp_verdict_driver_loop")
          and hasattr(tbw, "OPP_TOOL")
          and "scratch" in str(tbw.OPP_TOOL)
          and "opp_hindsight" in str(tbw.OPP_TOOL)
          and "import opp_hindsight" not in
          (BASE / "tidebar_weekly.py").read_text(encoding="utf-8"))
    check("1b.2 the runner parses the PRE-REGISTERED verdict and the "
          "opp census from the grader's own bytes (no verdict line, "
          "no census — nothing trusted)",
          hasattr(tbw, "_parse_opp_verdict")
          and ">>> " in tbw._parse_opp_verdict.__doc__
          and "champion-opportunistic" in tbw._parse_opp_verdict.__doc__)
    check("1b.3 the verdict section PREPENDS to the opp report (the "
          "newest verdict is always at the top — the tide-bar "
          "convention) and rotates at the family cap",
          hasattr(tbw, "_append_opp_section")
          and "newest" in tbw._append_opp_section.__doc__
          and hasattr(tbw, "OPP_REPORT_FILE")
          and "opp_hindsight_report.txt" in str(tbw.OPP_REPORT_FILE))
    check("1b.4 the scanner runs the driver (the family wiring: own "
          "thread, daemon, a broken pass never takes the funnel down)",
          "opp_verdict_driver_loop" in
          (BASE / "background_scanner.py").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as _opp_sbx:
        _old_opp = (tbw.OPP_TOOL, tbw.OPP_REPORT_FILE,
                    tbw.OPP_SNAP_FILE, tbw.OPP_CACHE_DB)
        try:
            tbw.OPP_TOOL = Path(_opp_sbx) / "opp_hindsight.py"
            tbw.OPP_REPORT_FILE = Path(_opp_sbx) / "opp_hindsight_report.txt"
            tbw.OPP_SNAP_FILE = Path(_opp_sbx) / "signal_snapshots.jsonl"
            tbw.OPP_CACHE_DB = Path(_opp_sbx) / "ohlcv_cache.db"
            tbw.OPP_TOOL.write_text(
                "import sys\n"
                "print('fire census (all routes, ledger-wide): '"
                " + sys.argv[1])\n"
                "print('  >>> KEEP-PROVISIONAL')\n"
                "print('  champion-opportunistic   n=0   payers=0')\n",
                encoding="utf-8")
            parsed = tbw._parse_opp_verdict(
                "THE OPP HINDSIGHT\n  >>> RETAIN-TIGHTEN\n"
                "  champion-opportunistic   n=12   payers=7\n")
            check("1b.5 the parse lifts the verdict text and the opp "
                  "census out of the grader's bytes",
                  parsed.get("verdict") == "RETAIN-TIGHTEN"
                  and parsed.get("n") == 12)
            ok1, _s1 = tbw._append_opp_section(
                "  >>> KEEP-PROVISIONAL\nfirst graded body")
            ok2, _s2 = tbw._append_opp_section(
                "  >>> RETAIN-TIGHTEN\nsecond graded body")
            _txt = tbw.OPP_REPORT_FILE.read_text(encoding="utf-8")
            check("1b.6 two passes prepend (second on top), each "
                  "section carrying its verdict line and the grader's "
                  "own body",
                  ok1 and ok2
                  and _txt.index("second graded body")
                  < _txt.index("first graded body")
                  and "verdict: RETAIN-TIGHTEN" in _txt
                  and "verdict: KEEP-PROVISIONAL" in _txt)
            tbw.OPP_TOOL.unlink()
            ok3, _sec3 = tbw._append_opp_section()
            check("1b.7 the grader GONE (untracked scratch swept) fails "
                  "SAFE: the run returns not-ok, no section is written, "
                  "the last report stands",
                  ok3 is False and _sec3 == ""
                  and "never lands" not in
                  tbw.OPP_REPORT_FILE.read_text(encoding="utf-8"))
            tbw.OPP_TOOL.write_text(
                "import sys\nprint('broken')\n"
                "print('  >>> SOMETHING-ELSE')\nsys.exit(0)\n",
                encoding="utf-8")
            ok4, _sec4 = tbw._append_opp_section()
            ok5, _sec5 = tbw._append_opp_section(
                "  >>> KEEP-PROVISIONAL-TYPO\nbody")
            check("1b.8 only the FOUR PRE-REGISTERED verdicts parse "
                  "(a '>>>' line that is not the contract's verdict — "
                  "or no verdict line at all — writes nothing, never a "
                  "fake verdict)",
                  ok4 is False and _sec4 == ""
                  and ok5 is False and _sec5 == ""
                  and "no parse no write" not in
                  tbw.OPP_REPORT_FILE.read_text(encoding="utf-8"))
            # (the reviewer minor, Oct 2 backfill: the cap was measured
            # in BYTES but the truncation sliced CHARACTERS — multibyte
            # content could exceed the byte cap on disk. The truncation
            # now cuts the ENCODED bytes and decodes with invalid
            # trailing bytes ignored — the file never exceeds the cap.)
            _big = "  >>> KEEP\n" + ("\u2014\u00fc\u00ef\u2014" * 60_000)
            okT, _ = tbw._append_opp_section(_big)
            _tbytes = tbw.OPP_REPORT_FILE.read_bytes()
            check("1b.9 the rotation is BYTE-exact (a multibyte body "
                  "heavy enough to blow the cap still leaves the file "
                  "at or under it)",
                  okT is True
                  and len(_big.encode("utf-8")) > tbw.REPORT_MAX_BYTES
                  and len(_tbytes) <= tbw.REPORT_MAX_BYTES)
        finally:
            (tbw.OPP_TOOL, tbw.OPP_REPORT_FILE,
             tbw.OPP_SNAP_FILE, tbw.OPP_CACHE_DB) = _old_opp

    # ── 2. the grading math ──────────────────────────────────────────
    g = tbw._grade([(80.0, True), (85.0, False), (60.0, True), (95.0, True)],
                   80.0)
    check("_grade fires only at-or-above the bar",
          g["fires"] == 3 and all(q >= 80 for q, _ in
                                  [(80.0, 1), (85.0, 1), (95.0, 1)]), g)
    import hindsight_scorer as _hs
    check("_grade prices payers with the house convention (payers only, "
          "size-multiplied)",
          abs(g["usd"] - (1055 * 0.015 * (_hs.brain_size_mult(80.0)
                                          + _hs.brain_size_mult(95.0))))
          < 0.01, g["usd"])
    g0 = tbw._grade([(60.0, True)], 80.0)
    check("nothing over the bar -> zero fires, zero dollars",
          g0["fires"] == 0 and g0["usd"] == 0.0, g0)
    check("a None bar grades to None (fail-closed)",
          tbw._grade([(90.0, True)], None) is None)

    # ── 3. the counterfactual quantile matches learn()'s math ────────
    import tide_learn as _tl
    qbar = tbw._quantile_bar_as_learn("OUT", MODEL)
    rate = MODEL["states"]["OUT"]["payer_rate"]
    scores = _tl._funnel_score_quantiles()
    expect = round(max(_tl.BAR_MIN, min(_tl.BAR_MAX,
                    _tl._quantile(scores, 1.0 - rate))), 1)
    check("the quantile counterfactual equals learn()'s own bar",
          qbar == expect, (qbar, expect))
    # THE DISCRIMINATING HALF (the Copilot [high] on b850e3e): the
    # counterfactual models the QUANTILE recipe, whose ship band is
    # [BAR_MIN, BAR_MAX] = [86, 97] — a different band than the outcome
    # ladder's 68 floor. A state whose raw quantile lands under 86 must
    # clamp to BAR_MIN: the outcome floor would show a bar the quantile
    # recipe could never ship, flattering the live outcome bar and
    # distorting the flip comparison. (The main fixture's quantile sits
    # above 86, where both floors agree — vacuous on the floor question
    # — hence this sub-band fixture.)
    _m_lo = {"states": {"OUT": dict(MODEL["states"]["OUT"],
                                    payer_rate=0.97)}}
    qbar_lo = tbw._quantile_bar_as_learn("OUT", _m_lo)
    raw_lo = _tl._quantile(scores, 1.0 - 0.97)
    expect_lo = round(max(_tl.BAR_MIN, min(_tl.BAR_MAX, raw_lo)), 1)
    check("a sub-band quantile clamps to the QUANTILE recipe's floor "
          "(BAR_MIN 86, not the outcome ladder's 68 — the recipes ship "
          "different bands)",
          qbar_lo == expect_lo and qbar_lo >= _tl.BAR_MIN
          and raw_lo < _tl.BAR_MIN,
          (qbar_lo, expect_lo, round(raw_lo, 1)))

    # ── 4. a full pass: both bars, the delta, the fail-safes ─────────
    real_week = tbw._week_candidates
    tbw._week_candidates = lambda: ([("OUT", 85.0, True), ("OUT", 90.0, False),
                                     ("FLAT", 95.0, True)], None)
    real_fit = _tl._outcome_fit_bar
    _tl._outcome_fit_bar = lambda st, sc, arr_cache=None: (
        {"bar": 84.0, "ev_o": 2.0, "ev_q": 1.0, "n": 100,
         "payer_rate": 0.1, "state": st} if st == "FLAT" else None)
    import tide_learn as _tl2
    real_q = _tl2._funnel_score_quantiles
    _tl2._funnel_score_quantiles = lambda: [50.0] * 60
    s = tbw.report_now()
    txt = tbw.OUT_FILE.read_text(encoding="utf-8")
    check("the pass graded the scripted week without error",
          "err" not in s, s)
    check("OUT's section shows both bars and the $/fire delta",
          "IN FORCE  bar 80" in txt and "quantile  bar" in txt
          and "Δ$/fire" in txt, txt[:400])
    check("FLAT (quantile in force) counterfactuals the outcome fit",
          "outcome-fitted bar 84" in txt, txt[:600])
    check("RISING (no in-force bar) is named, not priced",
          "RISING: no grade (no in-force bar)" in txt, txt[:800])
    check("the report carries the REAL FILLS section with the coverage stamp",
          "REAL FILLS" in txt and "fire-event coverage begins" in txt
          and "realized $+5.50" in txt, txt[:1200])

    # ── 4b. REAL FILLS: the join's contract ─────────────────────────
    fills, cov, why = tbw._fired_fills_this_week()
    check("fills join FORWARD in time only (the before-fire fill is excluded)",
          fills is not None and fills["RISING"]["fills"] == 0
          and fills["RISING"]["unfilled"] == 1, (fills, why))
    check("the FLAT fire joined its fill and its winner",
          fills["FLAT"]["fills"] == 1 and fills["FLAT"]["winners"] == 1
          and abs(fills["FLAT"]["pnl"] - 5.5) < 1e-9, fills)
    check("an unfilled payer is paper-graded at the fire's real spend",
          fills["RISING"]["unfilled_payers"] == 1
          and abs(fills["RISING"]["usd_if_paid"] - 200 * 0.015) < 1e-9, fills)
    check("suppressed records never enter the fills layer",
          set(fills) == {"FLAT", "RISING"}, set(fills))
    check("coverage begins at the earliest fire event",
          cov is not None and abs((cov - _T0).total_seconds()) < 5, cov)
    # the fills ride the history record so the trend can be replayed
    recs_after = tbw._load_history()
    check("the week's fills ride the history record (compact)",
          bool(recs_after)
          and recs_after[-1].get("fills") is not None
          and recs_after[-1]["fills"].get("FLAT") == {"fills": 1, "pnl": 5.5}
          and "OUT" not in recs_after[-1]["fills"],
          recs_after[-1].get("fills") if recs_after else None)
    check("the sidecar carries the fills trend beside the edge trend",
          (s.get("fills_trend") or {}).get("FLAT") == [5.5]
          and s["fills_trend"]["RISING"] == [0.0]
          and s["fills_trend"]["OUT"] == [None], s.get("fills_trend"))
    # ── 4d. THE RETUNE VALVE'S RECKONING (Oct 5, the gate-quality
    #    audit's recalibration): the audit priced the week's tide-out
    #    funnel end-to-end — the learned OUT bar is EV-max on BOTH
    #    contracts (60-min fit AND the 7h plate), so the BAR is not
    #    the defect; the dial's TIDE_RETUNE_DAILY_CAP override (1 vs
    #    the code default 3) is the binding constraint. The cap stays
    #    the OPERATOR'S call (the deep read burns slots at a ~5:1
    #    dud:payer bound) — so the recalibration is EVIDENCE: the
    #    weekly report now prices the valve's band, the slot budget,
    #    and the slots the ledger says were spent, and names the
    #    binding constraint with a verdict. The report prices the
    #    dial; it never moves it.
    check("4d.0 the reckoning rides the override seam (the drives "
          "never touch the live dial) and prices, never moves",
          tbw.RETUNE_DIAL_OVERRIDE is None
          and callable(tbw._retune_reckoning)
          and "never moves it" in (tbw._retune_reckoning.__doc__ or "")
          and "_retune_reckoning(by_state_ts.get(\"OUT\")" in
          (BASE / "tidebar_weekly.py").read_text(encoding="utf-8"))
    _real_override = tbw.RETUNE_DIAL_OVERRIDE
    try:
        # fixture date helper: an October date inside the 7-day window
        # (days rotate — only the per-day GROUPING matters to the math)
        def _tsd(d, h=12):
            return f"2026-10-{d:02d}T{h:02d}:00:00+00:00"
        # dial OFF: the wall stands alone, nothing to price
        tbw.RETUNE_DIAL_OVERRIDE = {"armed": False}
        _off = tbw._retune_reckoning([(90.0, True)], 86.0, "t-off")
        check("4d.1 a dark dial prices nothing (the wall stands alone; "
              "no band, no slots, no invented numbers)",
              _off is not None and _off["summary"]["verdict"] == "DIAL OFF"
              and "wall stands alone" in "\n".join(_off["lines"]), _off)
        # armed with headroom: 2 band candidates on 2 dates, cap 2/day
        tbw.RETUNE_DIAL_OVERRIDE = {"armed": True, "quick_min": 66.0,
                                    "cap": 2, "spent_today": 1}
        _suf = tbw._retune_reckoning(
            [(70.0, True, _tsd(2)), (80.0, True, _tsd(3))], 86.0, "t-suf")
        check("4d.2 an armed dial with headroom says so (cap SUFFICES, "
              "the naive budget named beside the per-day bound, the "
              "code default named for the operator, today's ledger "
              "spend carried)",
              _suf is not None
              and _suf["summary"]["verdict"].startswith("CAP SUFFICES")
              and _suf["summary"]["slots"] == 14
              and _suf["summary"].get("releasable") == 2
              and _suf["summary"].get("dates") == 2
              and _suf["summary"]["slots_spent_today"] == 1
              and "default cap is 3" in "\n".join(_suf["lines"]), _suf)
        # the audit's own week: 8 band candidates (3 payers) vs cap 1
        tbw.RETUNE_DIAL_OVERRIDE = {"armed": True, "quick_min": 66.0,
                                    "cap": 1, "spent_today": None}
        _bind = tbw._retune_reckoning(
            [(72.0, False), (74.0, True), (75.0, False), (78.0, False),
             (80.0, False), (82.0, True), (84.0, False), (85.0, True)],
            86.0, "t-bind")
        check("4d.3 the audit's binding shape (the Oct-2 week: 8 band "
              "candidates, 3 payers, cap 1/day = 7 slots) renders "
              "SLOTS BIND with the dial named as the constraint",
              _bind is not None
              and _bind["summary"]["band"]["fires"] == 8
              and _bind["summary"]["band"]["payers"] == 3
              and _bind["summary"]["slots"] == 7
              and "SLOTS BIND" in _bind["summary"]["verdict"]
              and "binding" in _bind["summary"]["verdict"]
              and "dial 1" in _bind["summary"]["verdict"], _bind)
        check("4d.4 an unreadable day ledger reads as unreadable (the "
              "fail-closed posture — never a fake 0 spent)",
              _bind is not None
              and _bind["summary"]["slots_spent_today"] is None
              and "unreadable (fail-closed)" in "\n".join(_bind["lines"]),
              _bind)
        # NO BAND: the valve's floor above everything seen
        tbw.RETUNE_DIAL_OVERRIDE = {"armed": True, "quick_min": 90.0,
                                    "cap": 1, "spent_today": None}
        _none = tbw._retune_reckoning([(80.0, True)], 86.0, "t-none")
        check("4d.5 a floor above the whole week prices NO BAND (no "
              "candidates, no invented verdict)",
              _none is not None
              and _none["summary"]["verdict"].startswith("NO BAND")
              and _none["summary"]["band"]["fires"] == 0, _none)
        # zero-EV band: slots bind but the data refuses the widening case
        # (cap 0 — a legitimately closed valve: 0 slots vs 3 candidates)
        tbw.RETUNE_DIAL_OVERRIDE = {"armed": True, "quick_min": 66.0,
                                    "cap": 0, "spent_today": None}
        _dud = tbw._retune_reckoning(
            [(70.0, False), (75.0, False), (80.0, False)], 86.0, "t-dud")
        check("4d.6 a dud band never argues for widening (SLOTS BIND "
              "with no pricing case — the reckoning is evidence, not a "
              "sales pitch)",
              _dud is not None
              and "no pricing case" in _dud["summary"]["verdict"]
              and _dud["summary"]["band"]["usd_per_fire"] == 0.0, _dud)
        # the valve-only world: no in-force bar, the band is everything
        _valve = tbw._retune_reckoning(
            [(70.0, True), (95.0, False)], None, "t-valve")
        check("4d.7 a cold learner (no bar) prices the valve as the "
              "whole gate (band top 100, offered 2)",
              _valve is not None and "100" in
              "\n".join(_valve["lines"])
              and _valve["summary"]["band_offered"] == 2, _valve)
        # the FULL PASS embeds the reckoning: report text + sidecar
        tbw.RETUNE_DIAL_OVERRIDE = {"armed": True, "quick_min": 66.0,
                                    "cap": 1, "spent_today": 0}
        _s = tbw.report_now()
        _txt_rr = tbw.OUT_FILE.read_text(encoding="utf-8")
        check("4d.8 the weekly pass embeds the reckoning after the "
              "grades (report text carries the section + verdict; the "
              "sidecar ships retune_reckoning for the MASTER plate)",
              "THE RETUNE VALVE'S RECKONING" in _txt_rr
              and _txt_rr.index("THE RETUNE VALVE'S RECKONING")
              > _txt_rr.index("IN FORCE  bar 80")
              and isinstance(_s.get("retune_reckoning"), dict)
              and _s["retune_reckoning"]["verdict"]
              in _txt_rr, (_s.get("retune_reckoning"),))
        check("4d.9 the sidecar's verdict matches the printed one (the "
              "plate and the report can never disagree)",
              isinstance(_s.get("retune_reckoning"), dict)
              and _s["retune_reckoning"]["dial"].get("cap") == 1
              and isinstance(_s["retune_reckoning"].get("band"), dict)
              and "verdict" in _s["retune_reckoning"],
              _s.get("retune_reckoning"))
        # the default-cap read: the real module's constant, and the
        # fail-soft when background_scanner is junk
        check("4d.10 the code default cap reads from the scanner "
              "constant (3, the study's value) and fails soft",
              tbw._retune_default_cap() == 3,
              tbw._retune_default_cap())
        # ── the commit-review [medium]s, cured red-first ────────────
        # (1) cap x 7 fungible slots is WRONG: a 7-day window touches
        # 8 calendar dates (the day ledger's key) and candidates
        # CLUSTER — the honest bound is SUM(min(cap, n_date)). (2) the
        # "EV case" must name the house convention (payers only, gross
        # of costs), never imply net P&L.
        _cluster = tbw._retune_reckoning(
            [(70.0, True, _tsd(2)), (72.0, False, _tsd(2, 13)),
             (74.0, True, _tsd(2, 14)), (76.0, False, _tsd(2, 15)),
             (78.0, True, _tsd(2, 16)), (80.0, False, _tsd(3)),
             (82.0, True, _tsd(3, 13)), (84.0, False, _tsd(4))],
            86.0, "t-cluster")
        check("4d.11 CLUSTERED candidates bind per-day (8 band "
              "candidates on 3 dates at cap 1/day = 3 releasable of 8 "
              "— the fungible cap x 7 said 7 slots; the per-day math "
              "is the honest bound)",
              _cluster is not None
              and _cluster["summary"]["verdict"].startswith("SLOTS BIND")
              and "only 3 of them releasable" in _cluster["summary"]["verdict"]
              and "clustering" in _cluster["summary"]["verdict"]
              and _cluster["summary"].get("releasable") == 3
              and _cluster["summary"].get("dates") == 3,
              _cluster["summary"] if _cluster else None)
        _spread = tbw._retune_reckoning(
            [(70.0, True, _tsd(28)), (72.0, False, _tsd(29)),
             (74.0, True, _tsd(30)), (76.0, False, _tsd(1)),
             (78.0, True, _tsd(2)), (80.0, False, _tsd(3)),
             (82.0, True, _tsd(4)), (84.0, False, _tsd(5))],
            86.0, "t-spread")
        check("4d.12 SPREAD candidates suffice per-day (the same 8 "
              "candidates on 8 dates at cap 1 = 8 releasable — the "
              "verdict FLIPS versus the clustered week, which the "
              "fungible cap x 7 could never see)",
              _spread is not None
              and _spread["summary"]["verdict"].startswith("CAP SUFFICES")
              and _spread["summary"].get("releasable") == 8
              and _spread["summary"].get("dates") == 8,
              _spread["summary"] if _spread else None)
        _dud2 = tbw._retune_reckoning(
            [(70.0, False, _tsd(2)), (75.0, False, _tsd(2, 13))],
            86.0, "t-dud2")
        check("4d.13 the EV case names its convention (payers only, "
              "gross of costs — a pricing case, not net P&L; the "
              "dud band argues nothing)",
              _dud2 is not None
              and "no pricing case" in _dud2["summary"]["verdict"]
              and "payers only" in _dud2["summary"]["verdict"]
              and "gross of costs" in _dud2["summary"]["verdict"],
              _dud2["summary"] if _dud2 else None)
        _bind2 = tbw._retune_reckoning(
            [(70.0, True, _tsd(2)), (75.0, True, _tsd(2, 13))], 86.0,
            "t-bind2")
        check("4d.14 a binding band with payers still prices the "
              "convention honestly (SLOTS BIND carries the payers-"
              "only / gross-of-costs naming beside the dollars)",
              _bind2 is not None
              and _bind2["summary"]["verdict"].startswith("SLOTS BIND")
              and "payers only" in _bind2["summary"]["verdict"]
              and "gross of costs" in _bind2["summary"]["verdict"]
              and "REAL FILLS" in _bind2["summary"]["verdict"],
              _bind2["summary"] if _bind2 else None)
        # ── the follow-up review [medium]: production candidates ride
        # (state, quick, payer) with NO date, so the per-day bound
        # degenerated to min(cap, n) under one 'unknown' date and could
        # falsely report BIND on spread-out candidates. The join must
        # carry each candidate's own ts through to the reckoning.
        _real_wc_4d = tbw._week_candidates
        try:
            tbw.RETUNE_DIAL_OVERRIDE = {"armed": True, "quick_min": 66.0,
                                        "cap": 1, "spent_today": 0}
            tbw._week_candidates = lambda: ([
                ("OUT", 70.0, True, _tsd(2, 10)),
                ("OUT", 72.0, False, _tsd(2, 11)),
                ("OUT", 74.0, True, _tsd(3, 10)),
                ("OUT", 76.0, False, _tsd(3, 11))], None)
            _s15 = tbw.report_now()
            _rr15 = _s15.get("retune_reckoning") or {}
            check("4d.15 the PRODUCTION join carries each candidate's "
                  "date through to the reckoning (4-tuple rows: 4 band "
                  "candidates clustered on 2 dates at cap 1 = 2 "
                  "releasable — the unknown-date degenerate said 1, and "
                  "the old 3-name unpack crashed the pass outright)",
                  "err" not in _s15
                  and _rr15.get("releasable") == 2
                  and _rr15.get("dates") == 2
                  and "only 2 of them releasable" in (_rr15.get("verdict") or ""),
                  (_s15.get("err"), _rr15))
        finally:
            tbw._week_candidates = _real_wc_4d
    finally:
        # THE DARK-VALVE POSTURE: the rest of this suite runs with the
        # dial override OFF — no later leg ever reads the production
        # dial json or the live day ledger (leak-audit: the seam is a
        # module attr; the process exits after the suite)
        tbw.RETUNE_DIAL_OVERRIDE = {"armed": False}

    # ── THE REGIME MARKS: flip events projected onto the trend ──────
    real_evs_m = tbw.FLIP_EVENTS_FILE
    tbw.FLIP_EVENTS_FILE = sandbox / "flip_events_marks.jsonl"
    try:
        recs_m = tbw._load_history()
        tbw._persist_flip_events(
            {"OUT": {"action": "flip", "losing_weeks": 3,
                     "roll_delta": -1.0,
                     "evidence": [{"week": recs_m[0]["week"],
                                   "fit_edge": -1.0}]}},
            recs_m[0]["week"], "tsm")
        marks = tbw._trend_marks(recs_m)
        wk0 = recs_m[0]["week"]
        check("the flip event lands on its own week's series slot",
              marks["OUT"][0] == f"flip@{wk0}"
              and all(m is None for m in marks["OUT"][1:])
              and all(set(marks[st]) == {None} for st in ("FLAT", "RISING")),
              marks)
        s_m = tbw.report_now()
        check("the sidecar carries the regime marks beside the trends",
              s_m.get("flip_marks", {}).get("OUT")[0] == f"flip@{wk0}",
              s_m.get("flip_marks"))
        # ── THE BENCH_WARN SIDECAR (Sep 12): the scanner's warn rides
        #    the JSON sidecar, so the MASTER plate shows the pass's own
        #    warning — log and plate can never disagree ──────────────
        check("every sidecar carries the bench_warn key (None, or a "
              "coherent {state, phrase} the plate can render)",
              "bench_warn" in s_m
              and (s_m["bench_warn"] is None
                   or (isinstance(s_m["bench_warn"], dict)
                       and s_m["bench_warn"].get("state")
                       and s_m["bench_warn"].get("phrase"))),
              s_m.get("bench_warn"))
        txt_before_failsafe = len(tbw.OUT_FILE.read_text(encoding="utf-8"))
    finally:
        tbw.FLIP_EVENTS_FILE = real_evs_m

    # ── 4c. THE ROLLING GRADE + THE AUTO-FLIP ────────────────────────
    import json as _json
    def _mkweek(tag, deltas, src="quantile"):
        """A week record: per-state in_force/other $/fire from deltas,
        with other_src = src (quantile = the fit is in force;
        outcome-fitted = the quantile is live, i.e. flipped regime)."""
        states = {}
        for st, (di, do) in deltas.items():
            states[st] = {"in_force": {"fires": 10, "payers": 3, "rate": 0.3,
                                       "usd": 10 * di, "usd_per_fire": di},
                          "other": {"fires": 10, "payers": 3, "rate": 0.3,
                                    "usd": 10 * do, "usd_per_fire": do},
                          "other_src": src}
        return {"week": tag, "generated_ts": tag, "states": states}
    H = [_mkweek("2026-W36", {"OUT": (2.0, 1.0)}),
         _mkweek("2026-W37", {"OUT": (1.0, 1.5)}),
         _mkweek("2026-W38", {"OUT": (1.0, 2.0)}),
         _mkweek("2026-W39", {"OUT": (1.0, 2.5)})]   # 3 losing weeks for OUT
    evs, fl = tbw._evaluate_flips(H, {})
    check("3 losing weeks + a negative pool -> the state AUTO-FLIPS",
          evs["OUT"] and evs["OUT"]["action"] == "flip"
          and fl["OUT"]["flipped"] is True, (evs, fl))
    evs2, fl2 = tbw._evaluate_flips(H, fl)
    check("the flip persists (no re-flip while flipped)",
          evs2["OUT"] is None and fl2["OUT"]["flipped"] is True, evs2)
    # flipped regime (src outcome-fitted): in_force IS the quantile, the
    # fit's fires are counterfactual — the fit must WIN on that axis
    W2 = [_mkweek("2026-W40", {"OUT": (1.0, 3.0)}, src="outcome-fitted"),
          _mkweek("2026-W41", {"OUT": (1.0, 3.0)}, src="outcome-fitted")]
    evs3, fl3 = tbw._evaluate_flips(W2, {"OUT": {"flipped": True}})
    check("while flipped, 2 fit-winning counterfactual weeks + a positive "
          "pool -> FLIP-BACK",
          evs3["OUT"] and evs3["OUT"]["action"] == "flip_back"
          and fl3["OUT"]["flipped"] is False, (evs3, fl3))
    # ── POOL_MIN SHIPS (Sep 12): the sweep's pool threshold becomes a
    #    real gate constant, defaulting to 0.0 so the shipped arc is
    #    IDENTICAL to the old `rd > 0` gate. Pins: the default, the
    #    thin-pool deny at a raised dial, the inclusive boundary, and
    #    the shipped arc byte-identical at the default. ───────────────
    check("POOL_MIN ships defaulting to 0.0 (the old `rd > 0` gate, "
          "named and sweep-proven)", tbw.POOL_MIN == 0.0, tbw.POOL_MIN)
    _real_pm = tbw.POOL_MIN
    try:
        # a THIN recovery: the streak is 2 winning weeks and the pooled
        # fit edge is positive but tiny (+0.20/fire) — exactly the
        # premature re-arm the arc-3 sweep caught
        Wthin = [_mkweek("2026-W40", {"OUT": (3.0, 3.2)},
                         src="outcome-fitted"),
                 _mkweek("2026-W41", {"OUT": (3.0, 3.2)},
                         src="outcome-fitted")]
        tbw.POOL_MIN = 0.50
        evs_thin, fl_thin = tbw._evaluate_flips(
            Wthin, {"OUT": {"flipped": True}})
        check("with POOL_MIN raised, the same 2-week streak with a "
              "thin +0.20 pool is DENIED (the sweep's premature "
              "re-arm, gated)",
              evs_thin["OUT"] is None
              and fl_thin["OUT"]["flipped"] is True, (evs_thin, fl_thin))
        tbw.POOL_MIN = _real_pm
        evs_thin0, fl_thin0 = tbw._evaluate_flips(
            Wthin, {"OUT": {"flipped": True}})
        check("at the shipped default the SAME thin arc re-arms "
              "(behavior unchanged: rd > 0 with POOL_MIN=0.0)",
              evs_thin0["OUT"]
              and evs_thin0["OUT"]["action"] == "flip_back"
              and fl_thin0["OUT"]["flipped"] is False,
              (evs_thin0, fl_thin0))
        tbw.POOL_MIN = 0.50
        Wexact = [_mkweek("2026-W40", {"OUT": (3.0, 3.5)},
                          src="outcome-fitted"),
                  _mkweek("2026-W41", {"OUT": (3.0, 3.5)},
                          src="outcome-fitted")]
        evs_ex, fl_ex = tbw._evaluate_flips(
            Wexact, {"OUT": {"flipped": True}})
        check("the bar is inclusive: a pool exactly AT POOL_MIN "
              "(+0.50 = 0.50) re-arms (at least, not above)",
              evs_ex["OUT"] and evs_ex["OUT"]["action"] == "flip_back",
              (evs_ex, fl_ex))
        tbw.POOL_MIN = _real_pm
        evs_pm0, fl_pm0 = tbw._evaluate_flips(
            W2, {"OUT": {"flipped": True}})
        check("the shipped arc at the default is the old gate: "
              "W40+W41 (fit edge +2.00 each, pool +2.00) flip back "
              "with the same roll_delta",
              evs_pm0["OUT"] and evs_pm0["OUT"]["action"] == "flip_back"
              and evs_pm0["OUT"]["roll_delta"] == 2.0
              and fl_pm0["OUT"]["flipped"] is False,
              (evs_pm0, fl_pm0))
    finally:
        tbw.POOL_MIN = _real_pm
    check("the POOL_MIN pins restore the shipped default",
          tbw.POOL_MIN == 0.0, tbw.POOL_MIN)
    # ── THE PER-STATE POOL BAR (Sep 13): the learner, the override, ──
    # ── the gate, and the writer/reader contract                    ──
    # the learner itself: rich state earns a bar, thin stays None
    # (_mkweek's tuple is (in_force, other) $/fire — edge = in − other,
    # so (d + 3.0, 3.0) makes OUT's edge exactly d)
    Wl = [_mkweek(f"2026-W{i}", {"OUT": (d + 3.0, 3.0)})
          for i, d in [(36, 2.0), (37, 1.0), (38, 0.5), (39, 0.6),
                       (40, 0.4), (41, 0.5)]]   # OUT edges: 2.0,1.0,.5,.6,.4,.5 -> med .55 -> bar .75
    Fl = [_mkweek(f"2026-W{i}", {"FLAT": (0.2, 3.0)})
          for i in range(36, 42)]                  # FLAT edges: all -2.8 -> floor
    Wthin = [_mkweek("2026-W36", {"RISING": (1.0, 3.0)})]   # 1 edge week: thin
    learned = tbw.learn_pool_min(Wl + Fl + Wthin)
    check("the learner derives a state's bar from its OWN edge "
          "median, quantized up to the 0.25 family (med +0.55 -> "
          "+0.75) and floors a negative-median state",
          learned["OUT"]["pool_min"] == 0.75
          and learned["FLAT"]["pool_min"] is None
          and learned["RISING"]["pool_min"] is None
          and "thin" in learned["RISING"]["reason"], learned)
    # the shipped bar actually gates: a rd=+0.50 re-arm clears the
    # global 0.0 but stays benched under OUT's learned +0.75, and the
    # global world's event stamps its decision-time pool_bar
    W05 = [_mkweek("2026-W40", {"OUT": (3.5, 3.0)}),
           _mkweek("2026-W41", {"OUT": (3.5, 3.0)})]   # fit edge +0.50 each, rd +0.50
    _real_pmf = tbw.POOLMIN_LEARN_FILE
    try:
        tbw.POOLMIN_LEARN_FILE.write_text(
            _json.dumps({"states": {"OUT": {"pool_min": 0.75}}}),
            encoding="utf-8")
        evs_fbL, fl_fbL = tbw._evaluate_flips(
            W05, {"OUT": {"flipped": True}})
        tbw._set_learned_pool_min({})   # the global-bar world
        evs_fb0, fl_fb0 = tbw._evaluate_flips(
            W05, {"OUT": {"flipped": True}})
        tbw._set_learned_pool_min(None)
        check("a shipped learned bar gates ITS state (rd +0.50 < bar "
              "+0.75 stays benched; the global 0.0 world re-arms) "
              "and stamps its decision-time pool_bar",
              evs_fbL["OUT"] is None
              and fl_fbL["OUT"]["flipped"] is True
              and evs_fb0["OUT"] is not None
              and (evs_fb0["OUT"] or {}).get("pool_bar") == 0.0,
              (evs_fbL, fl_fbL, evs_fb0))
        # the state-isolation half: the learned bar binds OUT only —
        # both states carry re-arm-qualifying weeks here, so FLAT's
        # None event is the bar's doing, not missing data. (The bar
        # is in-range: the reader drops out-of-[0,1] entries rather
        # than trust a hand-edited seal-forever value.)
        tbw.POOLMIN_LEARN_FILE.write_text(
            _json.dumps({"states": {"FLAT": {"pool_min": 0.75}}}),
            encoding="utf-8")
        Wiso = [_mkweek("2026-W40", {"OUT": (3.5, 3.0),
                                     "FLAT": (3.5, 3.0)}),
                _mkweek("2026-W41", {"OUT": (3.5, 3.0),
                                     "FLAT": (3.5, 3.0)})]
        evs_iso, fl_iso = tbw._evaluate_flips(
            Wiso, {s: {"flipped": True}
                   for s in ("OUT", "FLAT", "RISING")})
        check("a learned bar binds only its own state (FLAT's bar "
              "holds FLAT benched while OUT re-arms on identical weeks)",
              evs_iso["OUT"] is not None and evs_iso["FLAT"] is None,
              (evs_iso, fl_iso))
        # the counterfactual walks' override: pinned worlds ride the
        # GLOBAL bar ({}), never the file-backed learned one — with
        # the rd=+0.50 fixture, a leaked file (0.75) would never
        # re-arm (None); the correctly pinned world re-arms at W41
        tbw.POOLMIN_LEARN_FILE.write_text(
            _json.dumps({"states": {"OUT": {"pool_min": 0.75}}}),
            encoding="utf-8")
        arm_global = tbw._gate_rearm_week(W05, "OUT", "2026-W39",
                                          pool_min=0.0)
        check("the counterfactual walk pins the global-bar world "
              "(a learned file never leaks into a pinned replay)",
              arm_global == "2026-W41", arm_global)
    finally:
        tbw._set_learned_pool_min(None)
        tbw.POOLMIN_LEARN_FILE = _real_pmf
    check("the learner-file sandbox restores production's path",
          tbw.POOLMIN_LEARN_FILE == _real_pmf, tbw.POOLMIN_LEARN_FILE)
    # the writer/reader contract: the gate reads EXACTLY the nested
    # shape report_now ships (states: {ST: {pool_min, ...}} with ts +
    # n_weeks provenance) — a format drift between writer and reader
    # would silently drop every shipped bar back to the global 0.0
    _real_pmf2 = tbw.POOLMIN_LEARN_FILE
    try:
        _ship_shape = {"learned_ts": "2026-09-13 00:00 UTC",
                       "n_weeks": 9,
                       "states": {
                           "OUT": {"pool_min": 0.75, "n_edges": 9,
                                   "median": 0.61, "reason": ""},
                           "FLAT": {"pool_min": None, "n_edges": 9,
                                    "median": -0.4,
                                    "reason": "median rounds to the "
                                              "floor"},
                           "RISING": {"pool_min": None, "n_edges": 2,
                                      "median": None,
                                      "reason": "thin (2/6 edge weeks)"}}}
        tbw.POOLMIN_LEARN_FILE = sandbox / "pm_roundtrip.json"
        tbw.POOLMIN_LEARN_FILE.write_text(
            _json.dumps(_ship_shape), encoding="utf-8")
        _rt = tbw._load_pool_min_map()
        check("the gate reads exactly the shape report_now ships "
              "(nested provenance wrapper honored; thin states fall "
              "back to the global dial)",
              _rt == {"OUT": 0.75}, _rt)
    finally:
        tbw.POOLMIN_LEARN_FILE = _real_pmf2
    # the byte-identical-today contract: with NO learner file (today's
    # steady state) the shipped gate re-arms the exact shipped arc with
    # the exact shipped stamp — the learner can only ever tighten via
    # shipped evidence, never by accident
    tbw.POOLMIN_LEARN_FILE = sandbox / "pm_absent.json"   # never written
    evs_absent, fl_absent = tbw._evaluate_flips(
        W2, {"OUT": {"flipped": True}})
    check("with no learner file the shipped gate is the OLD gate "
          "byte-for-byte (the shipped arc re-arms, pool_bar 0.0)",
          evs_absent["OUT"] is not None
          and evs_absent["OUT"]["action"] == "flip_back"
          and evs_absent["OUT"].get("pool_bar") == 0.0
          and fl_absent["OUT"]["flipped"] is False,
          (evs_absent, fl_absent))
    tbw.POOLMIN_LEARN_FILE = _real_pmf2
    # THE INVERSION PIN (the old code failed this): while flipped, a fit
    # LOSING to the live quantile must NOT re-arm the fit
    Wbad = [_mkweek("2026-W40", {"OUT": (3.0, 1.0)}, src="outcome-fitted"),
            _mkweek("2026-W41", {"OUT": (3.0, 1.0)}, src="outcome-fitted")]
    evs_bad, _ = tbw._evaluate_flips(Wbad, {"OUT": {"flipped": True}})
    check("while flipped, the fit LOSING to the quantile never flips back "
          "(the sign is regime-normalized)",
          evs_bad["OUT"] is None, evs_bad)
    # a regime change mid-history must not break or fake a streak
    Wa = _mkweek("2026-W38", {"OUT": (1.0, 3.0)})                    # fit 1 vs quant 3
    Wb = _mkweek("2026-W39", {"OUT": (3.0, 1.0)}, src="outcome-fitted")  # quant 3 vs fit 1
    check("the canonical edge is identical across a regime boundary",
          tbw._week_fit_edge(Wa, "OUT") == -2.0
          and tbw._week_fit_edge(Wb, "OUT") == -2.0,
          (tbw._week_fit_edge(Wa, "OUT"), tbw._week_fit_edge(Wb, "OUT")))
    evs_cross, _ = tbw._evaluate_flips([Wa, Wb], {})
    check("a streak counts across the regime boundary (not reset by a flip)",
          evs_cross["OUT"] is None, evs_cross)   # 2 weeks < FLIP_WEEKS
    # the audit trail: every decision lands with its evidence weeks
    evs_ev, fl_ev = tbw._evaluate_flips(H, {})
    saved = tbw._persist_flip_events(evs_ev, "2026-W39", "now")
    trail = tbw._load_flip_events()
    check("a fired flip persists with its evidence weeks",
          saved == 1 and len(trail) == 1 and trail[0]["state"] == "OUT"
          and trail[0]["action"] == "flip"
          and [d["week"] for d in trail[0]["evidence"]]
          == ["2026-W37", "2026-W38", "2026-W39"]
          and trail[0]["evidence"][0]["fit_edge"] == -0.5
          and trail[0]["evidence"][2]["fit_edge"] == -1.5, trail)
    check("the same decision never double-appends (dedupe)",
          tbw._persist_flip_events(evs_ev, "2026-W39", "now") == 0
          and len(tbw._load_flip_events()) == 1)
    evs_b2, _ = tbw._evaluate_flips(W2, {"OUT": {"flipped": True}})
    saved2 = tbw._persist_flip_events(evs_b2, "2026-W41", "now2")
    trail2 = tbw._load_flip_events()
    check("a flip-back persists with its counterfactual evidence",
          saved2 == 1 and trail2[-1]["action"] == "flip_back"
          and [d["week"] for d in trail2[-1]["evidence"]]
          == ["2026-W40", "2026-W41"]
          and trail2[-1]["evidence"][0]["fit_edge"] == 2.0, trail2)
    check("a no-event week persists nothing",
          tbw._persist_flip_events({"FLAT": None}, "2026-W42", "t") == 0
          and len(tbw._load_flip_events()) == 2)
    # ── THE FBW=3 COUNTERFACTUAL (Sep 12): would ONE more re-arm week
    #    have saved a costing re-arm? Pure replay of the shipped gate
    #    with FLIP_BACK_WEEKS=3, priced against the row's own aftermath
    #    dollars. Fixtures reuse the flip arc H above; the sandbox trail
    #    is [flip@W39, flip_back@W41], so divergences read clean. ────
    _ev_fb = {"action": "flip_back", "state": "OUT", "week": "2026-W41",
              "evidence": [{"week": "2026-W40", "fit_edge": 2.0},
                           {"week": "2026-W41", "fit_edge": 2.0}]}
    _am = lambda rn, rp, rt, cn, cp, ct, wks: {
        "status": "graded", "weeks": wks,
        "realized": {"bar": "fit", "fires": rn, "usd_per_fire": rp,
                     "usd_total": rt},
        "counterfactual": {"bar": "quantile", "fires": cn,
                           "usd_per_fire": cp, "usd_total": ct},
        "verdict": "the decision COST"}
    # never-arms: the re-arm bleeds live (edge -2.0) — no third winning
    # week anywhere, so the save is the decision's own avoided cost
    Hc_never = H + [_mkweek("2026-W40", {"OUT": (1.0, 3.0)},
                            src="outcome-fitted"),
                    _mkweek("2026-W41", {"OUT": (1.0, 3.0)},
                            src="outcome-fitted"),
                    _mkweek("2026-W42", {"OUT": (1.0, 3.0)}),
                    _mkweek("2026-W43", {"OUT": (1.0, 3.0)})]
    r_never = tbw.fbw3_counterfactual(
        Hc_never, _ev_fb, "OUT",
        _am(20, 1.0, 20.0, 20, 3.0, 60.0, 2))
    check("fbw3 never-arms: the extra week denies the re-arm entirely "
          "and prices the decision's own avoided cost (fit $1.00 vs "
          "quantile $3.00 over 20 fires = $40.00 saved)",
          r_never and r_never["status"] == "never-arms"
          and r_never["saved_per_fire"] == 2.0
          and r_never["saved_total"] == 40.0
          and r_never["weeks"] == 2, r_never)
    # denied (honest negative): the replay's third winning week arrives
    # at W45, but the delayed arm then trades into a collapse — the
    # extra week would have paid LESS, and the clause must say so
    Hc_neg = H + [_mkweek("2026-W40", {"OUT": (1.0, 3.0)},
                          src="outcome-fitted"),
                  _mkweek("2026-W41", {"OUT": (1.0, 3.0)},
                          src="outcome-fitted"),
                  _mkweek("2026-W42", {"OUT": (1.0, 3.0)}),
                  _mkweek("2026-W43", {"OUT": (3.0, 1.0)}),
                  _mkweek("2026-W44", {"OUT": (3.0, 1.0)}),
                  _mkweek("2026-W45", {"OUT": (3.0, 1.0)}),
                  _mkweek("2026-W46", {"OUT": (0.5, 3.5)}),
                  _mkweek("2026-W47", {"OUT": (0.5, 3.5)})]
    r_neg = tbw.fbw3_counterfactual(
        Hc_neg, _ev_fb, "OUT",
        _am(40, 2.5, 100.0, 40, 1.5, 60.0, 6))
    check("fbw3 denied: the replay holds the bench to W45 (the streak "
          "completes after W42's break) and prices the delayed arm "
          "honestly — it paid $0.50 vs the lived $2.50, saving NEGATIVE",
          r_neg and r_neg["status"] == "denied"
          and r_neg["arm_week"] == "2026-W45"
          and r_neg["delayed_fires"] == 20
          and r_neg["delayed_per_fire"] == 0.5
          and r_neg["saved_per_fire"] == -2.0
          and r_neg["saved_total"] == -40.0
          and r_neg["weeks"] == 2, r_neg)
    # denied (positive): same arc, the delayed arm trades into strength
    Hc_pos = Hc_neg[:6] + [_mkweek("2026-W42", {"OUT": (1.0, 3.0)}),
                           _mkweek("2026-W43", {"OUT": (3.0, 1.0)}),
                           _mkweek("2026-W44", {"OUT": (3.0, 1.0)}),
                           _mkweek("2026-W45", {"OUT": (3.0, 1.0)}),
                           _mkweek("2026-W46", {"OUT": (3.5, 1.0)}),
                           _mkweek("2026-W47", {"OUT": (3.5, 1.0)})]
    r_pos = tbw.fbw3_counterfactual(
        Hc_pos, _ev_fb, "OUT",
        _am(40, 2.5, 100.0, 40, 1.5, 60.0, 6))
    check("fbw3 denied (positive): the delayed arm pays $3.50 vs the "
          "lived $2.50 over 20 fires = $20.00 saved",
          r_pos and r_pos["status"] == "denied"
          and r_pos["saved_per_fire"] == 1.0
          and r_pos["saved_total"] == 20.0, r_pos)
    # delayed: the replay's arm lands on the ledger's last week (W45 —
    # the third winning week arrives exactly there) — too few post-arm
    # weeks have traded to price it
    r_del = tbw.fbw3_counterfactual(
        Hc_neg[:10], _ev_fb, "OUT",
        _am(40, 2.5, 100.0, 40, 1.5, 60.0, 6))
    check("fbw3 delayed: the denied re-arm sits on the last graded week "
          "and pends honestly",
          r_del and r_del["status"] == "delayed"
          and r_del["arm_week"] == "2026-W45", r_del)
    # same-week: the POOL GUARD held the lived arm (the W40 pool is
    # -0.50 with the streak at 2), crossing only at W41 — so FBW=3
    # re-arms on the same week; the week dial is not what denied this
    # arm; nothing to price. (While flipped, in_force IS the quantile:
    # di=1.0 live, do=2.5 the fit's counterfactual seat.)
    Hc_same = [_mkweek("2026-W35", {"OUT": (0.5, 3.0)}),
               _mkweek("2026-W36", {"OUT": (0.5, 3.0)}),
               _mkweek("2026-W37", {"OUT": (0.5, 3.5)}),
               _mkweek("2026-W38", {"OUT": (0.5, 3.5)}),
               _mkweek("2026-W39", {"OUT": (1.0, 2.5)},
                       src="outcome-fitted"),
               _mkweek("2026-W40", {"OUT": (1.0, 2.5)},
                       src="outcome-fitted"),
               _mkweek("2026-W41", {"OUT": (1.0, 2.5)},
                       src="outcome-fitted"),
               _mkweek("2026-W42", {"OUT": (1.0, 3.0)})]
    r_same = tbw.fbw3_counterfactual(
        Hc_same, _ev_fb, "OUT",
        _am(10, 1.0, 10.0, 10, 3.0, 30.0, 1))
    check("fbw3 same-week: the pool guard, not the week count, held "
          "this arm — the replay re-arms on the same week, nothing to "
          "save",
          r_same and r_same["status"] == "same-week", r_same)
    # ungradeable inputs stay silent; constants restore; flips aren't
    # re-arm questions
    check("fbw3 counterfactual is honest silence on ungradeable input "
          "(pending aftermath, flip events)",
          tbw.fbw3_counterfactual(Hc_never, _ev_fb, "OUT", None) is None
          and tbw.fbw3_counterfactual(
              Hc_never, _ev_fb, "OUT",
              {"status": "pending", "reason": "x"}) is None
          and tbw.fbw3_counterfactual(
              Hc_never, {"action": "flip", "state": "OUT",
                         "week": "2026-W39", "evidence": []},
              "OUT",
              _am(20, 1.0, 20.0, 20, 3.0, 60.0, 2)) is None)
    check("the fbw3 replay restores the shipped gate constants",
          (tbw.FLIP_BACK_WEEKS, tbw.FLIP_WEEKS, tbw.POOL_MIN)
          == (2, 3, 0.0),
          (tbw.FLIP_BACK_WEEKS, tbw.FLIP_WEEKS, tbw.POOL_MIN))
    # ── THE POOL-THRESHOLD COUNTERFACTUAL (Sep 13): the pool dial's
    #    own question on the same COSTING rows — would raising the bar
    #    to +0.25 have denied a thin-pool re-arm? Sibling of the FBW=3
    #    replay: only the pool minimum moves, the streak dial stays
    #    shipped. Same sandbox trail [flip@W39, flip_back@W41]. ──────
    # never-arms: the lived arm decided at W43 on a pool of +0.15
    # (under the bar) — two bleed weeks (−2.0) then two fit wins
    # (+2.3) pool to (−2−2+2.3+2.3)/4 = +0.15. Under the raised bar
    # the replay HOLDS at W43, and W44's edge break (−2.0, pool still
    # +0.15) never completes another streak-with-pool: the re-arm is
    # denied entirely — the decision's own avoided cost. (On flipped
    # records the tuple is (in_force=quantile, other=fit).)
    Pc_never = H + [_mkweek("2026-W40", {"OUT": (3.0, 1.0)},
                            src="outcome-fitted"),
                    _mkweek("2026-W41", {"OUT": (3.0, 1.0)},
                            src="outcome-fitted"),
                    _mkweek("2026-W42", {"OUT": (3.0, 5.3)},
                            src="outcome-fitted"),
                    _mkweek("2026-W43", {"OUT": (3.0, 5.3)},
                            src="outcome-fitted"),
                    _mkweek("2026-W44", {"OUT": (3.0, 1.0)},
                            src="outcome-fitted")]
    _ev_fb_thin = {"action": "flip_back", "state": "OUT",
                   "week": "2026-W43", "sim": False,
                   "evidence": [{"week": "2026-W42", "fit_edge": 2.3},
                                {"week": "2026-W43", "fit_edge": 2.3}]}
    _am1 = _am(20, 1.0, 20.0, 20, 3.0, 60.0, 1)
    r_pnever = tbw.poolcf_counterfactual(Pc_never, _ev_fb_thin, "OUT",
                                         _am1)
    check("poolcf never-arms: the lived arm's pool +0.15 is under the "
          "+0.25 bar and no later week ever clears it — the re-arm is "
          "denied entirely (fit $1.00 vs quantile $3.00 over 20 fires "
          "= $40.00 saved)",
          r_pnever and r_pnever["status"] == "never-arms"
          and r_pnever["saved_per_fire"] == 2.0
          and r_pnever["saved_total"] == 40.0
          and r_pnever["weeks"] == 1, r_pnever)
    # denied: the same +0.15 lived arm, but the fit keeps winning —
    # W44 stays a win (streak 3, pooled rd +1.225 ≥ 0.25), so the
    # raised bar re-arms at W44; the delayed arm then trades W45+W46
    # at $4.50 vs the lived arm's $1.00 — the denial saves $70.00
    Pc_denied = Pc_never[:8] + [
        _mkweek("2026-W44", {"OUT": (3.0, 5.3)}, src="outcome-fitted"),
        _mkweek("2026-W45", {"OUT": (4.5, 7.5)}, src="outcome-fitted"),
        _mkweek("2026-W46", {"OUT": (4.5, 7.5)}, src="outcome-fitted")]
    r_pden = tbw.poolcf_counterfactual(
        Pc_denied, _ev_fb_thin, "OUT",
        _am(10, 1.0, 10.0, 10, 3.0, 30.0, 1))
    check("poolcf denied: the raised bar holds the +0.15 arm and "
          "re-arms at W44 once the pool clears +0.25 — the delayed "
          "arm's $4.50 vs lived $1.00 over 20 fires saves $70.00",
          r_pden and r_pden["status"] == "denied"
          and r_pden["arm_week"] == "2026-W44"
          and r_pden["delayed_fires"] == 20
          and r_pden["delayed_per_fire"] == 4.5
          and r_pden["saved_per_fire"] == 3.5
          and r_pden["saved_total"] == 70.0, r_pden)
    # same-week: a FAT pooled decision (+0.65 = (−1−1+2.3+2.3)/4)
    # already clears the raised bar — the replay re-arms on the same
    # week; the pool dial is not what would have denied this arm. W44
    # (a bleed) exists so the decision is not the ledger's last week —
    # the post-decision guard demands a week after the arm to grade.
    Pc_fat = H + [_mkweek("2026-W40", {"OUT": (3.0, 2.0)},
                          src="outcome-fitted"),
                  _mkweek("2026-W41", {"OUT": (3.0, 2.0)},
                          src="outcome-fitted"),
                  _mkweek("2026-W42", {"OUT": (3.0, 5.3)},
                          src="outcome-fitted"),
                  _mkweek("2026-W43", {"OUT": (3.0, 5.3)},
                          src="outcome-fitted"),
                  _mkweek("2026-W44", {"OUT": (1.0, 3.0)},
                          src="outcome-fitted")]
    _ev_fb_fat = {"action": "flip_back", "state": "OUT",
                  "week": "2026-W43", "sim": False,
                  "roll_delta": 0.65,
                  "evidence": [{"week": "2026-W42", "fit_edge": 2.3},
                               {"week": "2026-W43", "fit_edge": 2.3}]}
    r_psame = tbw.poolcf_counterfactual(
        Pc_fat, _ev_fb_fat, "OUT",
        _am(10, 1.0, 10.0, 10, 3.0, 30.0, 1))
    check("poolcf same-week: a fat pooled decision (+0.65) clears the "
          "raised bar — the replay re-arms on the same week, nothing "
          "to save",
          r_psame and r_psame["status"] == "same-week"
          and r_psame["pool"] == 0.65, r_psame)
    # honest silence + purity: sims, flips, pending aftermaths and
    # missing post-decision weeks are None; the shipped constants and
    # the learned-bar override restore
    check("poolcf is honest silence on ungradeable input (sims, flips, "
          "pending aftermath, no post-decision week)",
          tbw.poolcf_counterfactual(
              Pc_fat, dict(_ev_fb_fat, sim=True), "OUT", _am1) is None
          and tbw.poolcf_counterfactual(
              Pc_fat, {"action": "flip", "state": "OUT",
                       "week": "2026-W39", "evidence": []},
              "OUT", _am1) is None
          and tbw.poolcf_counterfactual(
              Pc_fat, _ev_fb_fat, "OUT",
              {"status": "pending", "reason": "x"}) is None
          and tbw.poolcf_counterfactual(
              Pc_fat, _ev_fb_fat, "OUT", None) is None
          and tbw.poolcf_counterfactual(
              Pc_fat, dict(_ev_fb_fat, evidence=[]), "OUT",
              _am1) is None)
    check("the poolcf replay restores the shipped constants and the "
          "learned-bar override",
          (tbw.POOL_MIN, tbw.FLIP_BACK_WEEKS)
          == (0.0, 2)
          and tbw._LEARNED_POOL_MIN_OVERRIDE is None,
          (tbw.POOL_MIN, tbw.FLIP_BACK_WEEKS,
           tbw._LEARNED_POOL_MIN_OVERRIDE))
    # ── THE EVIDENCE-GATED DEFAULT (Sep 13): the arc-3 LIVE sweep ──
    #    on synthetic real re-arms (passed directly — the sandbox
    #    trail is never touched); the shipped 0.0 turns only on
    #    lived evidence, and the gate's resolution order is
    #    learned bar > evidence default > global 0.0. ──────────────
    _real_pmdf = tbw.POOLMIN_DEF_FILE
    _real_pmsf = tbw.POOLMIN_STAGED_FILE
    try:
        tbw.POOLMIN_DEF_FILE = sandbox / "poolmin_default.json"
        tbw.POOLMIN_STAGED_FILE = sandbox / "poolmin_staged.json"
        # zero evidence: the sweep keeps the shipped 0.0 and writes
        # nothing — no file, no default, no silent moves
        rp0 = tbw.recommend_pool_min([], [])
        check("the arc-3 live sweep holds 0.0 with no priced "
              "re-arms and ships no default file",
              rp0["recommendation"] is None and rp0["n_priced"] == 0
              and not tbw.POOLMIN_DEF_FILE.exists(), rp0)
        # three priced COSTING re-arms: every week is fit-losing
        # (fit $1.00 vs quantile $3.00 — on these un-flipped-shape
        # records the default quantile-src makes (1.0, 3.0) exactly
        # that), so no replay ever re-arms again — every denial is a
        # full-window save. Events space 3 weeks apart (arm, two
        # costing weeks, next arm): events 1-2 decided at rd +0.10
        # (both candidates deny, $40.00 each = (3−1)×20 fires);
        # event 3 at rd +0.30 escapes +0.25 but +0.50 denies it
        # ($40.00) — +0.25 nets $80, +0.50 nets $120, and the
        # recommendation is +0.50
        _rp_recs = [_mkweek(f"2026-W{w}", {"OUT": (1.0, 3.0)})
                    for w in range(37, 49)]
        def _rp_ev(wk, rd):
            return {"action": "flip_back", "state": "OUT",
                    "week": wk, "roll_delta": rd,
                    "evidence": [{"week": f"2026-W{int(wk[7:]) - 1}",
                                  "fit_edge": -2.0},
                                 {"week": wk, "fit_edge": -2.0}]}
        _rp_evs = [_rp_ev("2026-W40", 0.1), _rp_ev("2026-W43", 0.1),
                   _rp_ev("2026-W46", 0.3)]
        rp3 = tbw.recommend_pool_min(_rp_recs, _rp_evs)
        check("the arc-3 live sweep on stamps-mode evidence BLOCKS the "
              "raise: stamps cannot show WHERE in time a denial's "
              "dollars landed, so no per-week series means no "
              "stability proof — no proposal, no file (reconcile the "
              "walk first)",
              rp3["recommendation"] is None and rp3["stable"] is False
              and rp3["n_priced"] == 3
              and not tbw.POOLMIN_DEF_FILE.exists()
              and any("RAISE BLOCKED" in l and "per-week series" in l
                      for l in rp3["lines"]),
              rp3)
        # ── THE STABILITY GATE + THE HAND-OFF (Sep 13): walked-mode "
        #    fixtures — a raise proposes only when every rolling "
        #    window stays positive, and even then the machinery "
        #    NEVER writes the default file (apply by hand). ──
        _wp_flip = {"state": "FLAT", "action": "flip",
                    "week": "2026-W39", "roll_delta": -0.5,
                    "evidence": []}
        _wp_ev = {"state": "FLAT", "action": "flip_back",
                  "week": "2026-W41", "roll_delta": 0.3,
                  "winning_weeks": 2,
                  "evidence": [{"week": "2026-W40", "fit_edge": 0.2},
                               {"week": "2026-W41", "fit_edge": 0.4}]}
        def _wpmk(tag, fi, fo, src):
            return {"week": tag, "generated_ts": tag,
                    "states": {"FLAT": {
                        "in_force": {"fires": 10, "payers": 3,
                                     "rate": 0.3, "usd": 10 * fi,
                                     "usd_per_fire": fi},
                        "other": {"fires": 10, "payers": 3,
                                  "rate": 0.3, "usd": 10 * fo,
                                  "usd_per_fire": fo},
                        "other_src": src}}}
        # the pg_cost arc: benched W40 (mild win), re-arm W41 at pool
        # +0.30 (+0.25 passes, +0.50 denies), fit-losing W42-W43 — the
        # +0.50 denial's divergence is +$20.00 per week, all positive
        _wp_pay = [_wpmk("2026-W40", 2.0, 2.2, "outcome-fitted"),
                   _wpmk("2026-W41", 2.4, 2.0, "quantile"),
                   _wpmk("2026-W42", 1.0, 3.0, "quantile"),
                   _wpmk("2026-W43", 1.0, 3.0, "quantile")]
        rp_w = tbw.recommend_pool_min(_wp_pay, [_wp_flip, _wp_ev],
                                      min_events=1)
        check("the walked-mode raise PROPOSES and STAGES: every "
              "rolling window positive (4w and 8w over the +$20/week "
              "divergence), stable True, a proposal dict, the stage "
              "written (her confirm-to-apply is the only hand that "
              "moves the gate) — and NO default file",
              rp_w["recommendation"] == 0.5 and rp_w["stable"] is True
              and rp_w["proposal"] == {"pool_min": 0.5, "n_priced": 1,
                                       "net": 40.0, "mode": "walked"}
              and rp_w["staged"] is True
              and tbw.POOLMIN_STAGED_FILE.exists()
              and not tbw.POOLMIN_DEF_FILE.exists()
              and any("PROPOSAL (STAGED" in l
                      and "confirm-to-apply" in l
                      for l in rp_w["lines"]),
              (rp_w.get("recommendation"), rp_w.get("stable"),
               rp_w.get("proposal"), rp_w.get("staged"),
               rp_w["lines"][-1]))
        # the staged stage's fields: what the MASTER state reader and
        # the guarded apply both consume
        _staged_raw = _json.loads(
            tbw.POOLMIN_STAGED_FILE.read_text(encoding="utf-8"))
        check("the stage carries the bar, its priced count, its net, "
              "and its mode — everything the confirm-to-apply path "
              "validates",
              _staged_raw.get("pool_min") == 0.5
              and _staged_raw.get("n_priced") == 1
              and _staged_raw.get("net") == 40.0
              and _staged_raw.get("mode") == "walked",
              _staged_raw)
        tbw.POOLMIN_STAGED_FILE.unlink(missing_ok=True)
        # ── THE FBW DIAL JOINS THE DISCIPLINE (Sep 13): recommend_fbw
        #    runs the same gates on the streak axis. On _wp_pay both
        #    candidates deny the W41 re-arm (ww=2 < 3 and < 4), each
        #    netting +$40 — FBW=3 proposes; on _wp_stk the trailing
        #    window blocks exactly like the pool dial's hot streak.
        #    PROPOSAL-ONLY by nature: no stage file, no apply — a code
        #    change by her hand. ─────────────────────────────────────
        rf_w = tbw.recommend_fbw(_wp_pay, [_wp_flip, _wp_ev],
                                 min_events=1)
        check("the FBW walk PROPOSES: walked family, every rolling "
              "window positive (+$40 over the W42-W43 divergence), a "
              "proposal dict — and NO stage file (the streak dial's "
              "raise is advice for her hand, never an auto-move). On "
              "an exact net tie the more conservative candidate wins "
              "(FBW=4 over 3, the pool dial's own tiebreak)",
              rf_w["recommendation"] == 4 and rf_w["stable"] is True
              and rf_w["mode"] == "walked"
              and rf_w["proposal"] == {"fbw": 4, "n_priced": 1,
                                       "net": 40.0, "mode": "walked"}
              and not tbw.POOLMIN_STAGED_FILE.exists()
              and any("PROPOSAL (recommendation only" in l
                      and "FLIP_BACK_WEEKS -> 4" in l
                      for l in rf_w["lines"]),
              (rf_w.get("recommendation"), rf_w.get("stable"),
               rf_w.get("proposal"), rf_w["lines"][-1]))
        rf_0 = tbw.recommend_fbw([], [])
        check("the FBW walk holds with zero priced re-arms (the "
              "shipped FBW=2 has no evidence to move on)",
              rf_0["recommendation"] is None and rf_0["n_priced"] == 0
              and any("no priced real re-arms" in l
                      for l in rf_0["lines"]),
              rf_0["lines"])
        # the hot streak that ends negative: the same arc but the fit
        # recovers mild-win live W45-W48 (the walked world stays
        # benched — its own +0.30 pools keep failing the +0.50 bar),
        # so the tail of the divergence MISSES $4/week: the full sum
        # stays positive ($+60 early vs $-16 late) but the trailing
        # 4-week window is negative — blocked
        _wp_stk = _wp_pay + [
            _wpmk("2026-W45", 2.4, 2.0, "quantile"),
            _wpmk("2026-W46", 2.4, 2.0, "quantile"),
            _wpmk("2026-W47", 2.4, 2.0, "quantile"),
            _wpmk("2026-W48", 2.4, 2.0, "quantile")]
        rp_b = tbw.recommend_pool_min(_wp_stk, [_wp_flip, _wp_ev],
                                      min_events=1)
        check("the stability gate BLOCKS the hot streak: the full "
              "ledger nets positive but the trailing 4-week window is "
              "negative ($-16.00) — a dial that only paid in the past "
              "does not raise (no proposal, no file)",
              rp_b["recommendation"] is None and rp_b["stable"] is False
              and not tbw.POOLMIN_DEF_FILE.exists()
              and any("RAISE BLOCKED" in l and "$-16.00" in l
                      for l in rp_b["lines"]),
              rp_b)
        # the FBW stability gate BLOCKS the same hot streak: the
        # walked stream stays benched through the fit's recovered
        # W45-W48 weeks, so the trailing window misses the same
        # $4/week — a streak demand that only paid in the past does
        # not raise
        rf_b = tbw.recommend_fbw(_wp_stk, [_wp_flip, _wp_ev],
                                 min_events=1)
        check("the FBW stability gate BLOCKS the hot streak: the full "
              "ledger nets positive but the trailing window is "
              "negative ($-16.00) — a streak demand that only paid in "
              "the past does not raise",
              rf_b["recommendation"] is None and rf_b["stable"] is False
              and any("RAISE BLOCKED" in l and "$-16.00" in l
                      for l in rf_b["lines"]),
              rf_b)
        # the resolution order at the gate: the evidence default a
        # HAND-APPLIED proposal writes binds states without a learned
        # bar; a learned bar still wins; a pinned replay world
        # suppresses the default (the sweep itself never writes here)
        tbw.POOLMIN_DEF_FILE.write_text(
            _json.dumps({"pool_min": 0.5, "n_priced": 3, "net": 120.0,
                         "mode": "walked", "ts": "hand-applied"}),
            encoding="utf-8")
        tbw.POOLMIN_LEARN_FILE = sandbox / "poolmin_reso.json"
        tbw.POOLMIN_LEARN_FILE.write_text(
            _json.dumps({"states": {"FLAT": {"pool_min": 0.75}}}),
            encoding="utf-8")
        check("resolution order: learned bar > hand-applied evidence "
              "default > global 0.0 (FLAT's learned 0.75 wins, OUT "
              "rides the applied 0.50)",
              tbw._pool_min_for("FLAT") == 0.75
              and tbw._pool_min_for("OUT") == 0.5
              and tbw._pool_min_for("RISING") == 0.5,
              (tbw._pool_min_for("FLAT"), tbw._pool_min_for("OUT"),
               tbw._pool_min_for("RISING")))
        tbw._set_learned_pool_min({})
        _reso_pure = tbw._pool_min_for("OUT")
        tbw._set_learned_pool_min(None)
        check("a pinned replay world suppresses the evidence default "
              "(the counterfactuals' baseline is the SHIPPED bar)",
              _reso_pure == 0.0, _reso_pure)
    finally:
        tbw._set_learned_pool_min(None)
        tbw.POOLMIN_LEARN_FILE = _real_pmf2
        tbw.POOLMIN_DEF_FILE = _real_pmdf
        tbw.POOLMIN_STAGED_FILE = _real_pmsf
    check("the default-file sandbox restores production's path",
          tbw.POOLMIN_DEF_FILE == _real_pmdf
          and tbw.POOLMIN_STAGED_FILE == _real_pmsf
          and not _real_pmdf.exists()
          and not _real_pmsf.exists(),
          (str(tbw.POOLMIN_DEF_FILE), str(_real_pmdf)))
    # ── the SIM trail: the diag's worked example, machinery-inert ───
    n_sim = tbw.persist_sim_events([
        {"sim": True, "state": "OUT", "action": "flip",
         "week": "2026-W39", "ts": "simulated replay (2026-W39)",
         "roll_delta": -0.99,
         "evidence": [{"week": "2026-W37", "fit_edge": -0.52},
                      {"week": "2026-W38", "fit_edge": -1.22},
                      {"week": "2026-W39", "fit_edge": -1.22}],
         "sim_aftermath": {"status": "graded", "weeks": 6,
                           "realized": {"bar": "quantile", "fires": 10,
                                        "usd_per_fire": 4.8},
                           "counterfactual": {"bar": "fit", "fires": 10,
                                             "usd_per_fire": 3.6},
                           "verdict": "the decision PAID"}},
        {"sim": True, "state": "OUT", "action": "flip_back",
         "week": "2026-W45", "ts": "simulated replay (2026-W45)"},
        # an event without the sim mark must be refused outright
        {"state": "FLAT", "action": "flip", "week": "2026-W99"}])
    trail3 = tbw._load_flip_events()
    check("sim events persist with the sim mark (and only sim events)",
          n_sim == 2 and len(trail3) == 4
          and all(e.get("sim") for e in trail3[2:]), trail3)
    check("persist_sim_events is idempotent (a re-run adds nothing)",
          tbw.persist_sim_events(trail3[2:]) == 0
          and len(tbw._load_flip_events()) == 4)
    check("_real_flip_events excludes the sims (machinery-inert)",
          len(tbw._real_flip_events()) == 2)
    check("the trend marks never stamp a sim week (the real W39 event "
          "stamps; the sim-only W45 does not)",
          tbw._trend_marks(
              [{"week": "2026-W39"}, {"week": "2026-W45"}])["OUT"]
          == ["flip@2026-W39", None],
          tbw._trend_marks([{"week": "2026-W39"}, {"week": "2026-W45"}]))
    for e in trail3[2:]:
        trail3.remove(e)
    tbw._save_flip_events(trail3)   # restore the 2-event sandbox trail
    # ── the flip-grade driver: the monthly real-ledger grade ────────
    real_fg = tbw.FLIPGRADE_FILE
    tbw.FLIPGRADE_FILE = sandbox / "flipgrade.txt"
    # the open-bench sidecar + the flips file ride the same sandbox:
    # grade_ledger writes _flipgrade.json on every path now, and the
    # open-bench math reads the flips state (the production files were
    # leaked once — this pin's own incident)
    real_fgj = tbw.FLIPGRADE_JSON
    real_flipf = tbw.FLIP_FILE
    # the regime window rides the same sandbox — a completed grade
    # records its reckoning row, and the production window must never
    # hear a test grade's contributions
    real_rkwin = tbw.RECKONING_WINDOW_FILE
    tbw.RECKONING_WINDOW_FILE = sandbox / "reckoning_window.jsonl"
    tbw.FLIPGRADE_JSON = sandbox / "flipgrade.json"
    tbw.FLIP_FILE = sandbox / "flips.json"
    # a clean events slate: the audit-trail pins above appended events
    # to the SHARED sandbox trail — without this, the aftermath pins
    # would grade those stale decisions (the intermittent failure)
    real_ev_fg = tbw.FLIP_EVENTS_FILE
    tbw.FLIP_EVENTS_FILE = sandbox / "flip_events_grade.jsonl"
    try:
        s = tbw.grade_ledger()
        check("the flip grade HOLDS below the minimum history",
              "held" in s and s["weeks"] < tbw.FLIPGRADE_MIN_WEEKS
              and not tbw.FLIPGRADE_FILE.exists(), s)
        # two weeks with a real regime mix: W37 fit live (edge -1.0),
        # W38 flipped (raw Δ +1.0 with other_src outcome-fitted ->
        # canonical fit edge -2.0)
        Hg = [_mkweek("2026-W37", {"OUT": (1.0, 2.0)}),
              _mkweek("2026-W38", {"OUT": (3.0, 1.0)},
                      src="outcome-fitted")]
        Hg_real = sandbox / "history_grade.jsonl"
        Hg_real.write_text("\n".join(json.dumps(r) for r in Hg) + "\n",
                           encoding="utf-8")
        real_hist2 = tbw.HISTORY_FILE
        tbw.HISTORY_FILE = Hg_real
        try:
            s2 = tbw.grade_ledger()
            txtg = tbw.FLIPGRADE_FILE.read_text(encoding="utf-8")
            check("the flip grade grades from the real ledger once the "
                  "minimum history exists",
                  "held" not in s2 and s2.get("weeks") == 2
                  and "=== THE FLIP GRADE" in txtg, (s2, txtg[:300]))
            check("a flipped-regime week grades on the canonical axis "
                  "(raw +1.0 reads as a -2.0 fit edge)",
                  "-2.00" in txtg and "-1.00" in txtg, txtg[:600])
            check("the bench proximity line renders",
                  "bench proximity: 2/3 losing weeks" in txtg, txtg[:600])
            # an event with aftermath: append the event, then a week
            # after the decision (quantile live +3.0, fit cf +1.0)
            tbw._persist_flip_events(
                {"OUT": {"action": "flip", "losing_weeks": 3,
                         "roll_delta": -1.5,
                         "evidence": [{"week": "2026-W37", "fit_edge": -1.0}]}},
                "2026-W37", "ts")
            Hg.append(_mkweek("2026-W39", {"OUT": (3.0, 1.0)},
                              src="outcome-fitted"))
            Hg_real.write_text("\n".join(json.dumps(r) for r in Hg) + "\n",
                               encoding="utf-8")
            s3 = tbw.grade_ledger()
            txtg2 = tbw.FLIPGRADE_FILE.read_text(encoding="utf-8")
            check("the aftermath grades the DECISION's bar vs the benched "
                  "side (after a flip the quantile IS the live bar — the "
                  "old math mislabeled the benched fit as live and "
                  "inverted the verdict)",
                  "the quantile paid $+3.00/fire" in txtg2
                  and "the fit's counterfactual $+1.00/fire" in txtg2
                  and "the decision PAID" in txtg2, txtg2[:800])
            check("the newest grade sits at the top of the file",
                  txtg2.startswith("=== THE FLIP GRADE"), txtg2[:80])
            # the pending aftermath wording: a decision at the newest
            # week has no post-decision weeks yet
            tbw._persist_flip_events(
                {"OUT": {"action": "flip", "losing_weeks": 3,
                          "roll_delta": -0.5,
                          "evidence": [{"week": "2026-W39", "fit_edge": -0.5}]}},
                "2026-W39", "ts2")
            s4 = tbw.grade_ledger()
            txtg3 = tbw.FLIPGRADE_FILE.read_text(encoding="utf-8")
            check("an aftermath with no post-decision weeks is pending",
                  "aftermath is pending" in txtg3, txtg3[:800])
            # ── the SHARED aftermath helper (the trail's math) ──────
            amf = tbw._event_aftermath
            H_am = [_mkweek("2026-W37", {"OUT": (1.0, 1.0)}),   # decision wk
                    _mkweek("2026-W39", {"OUT": (2.0, 3.0)},
                            src="outcome-fitted"),               # q 2, f 3
                    _mkweek("2026-W40", {"OUT": (2.0, 3.0)},
                            src="outcome-fitted")]               # q 2, f 3
            # flip at W37, evidence W37 -> after = W39..W40: quantile
            # (in_force, flipped regime) realized (10x2 + 10x2)/20 = 2.0
            # vs the fit's counterfactual (10x3 + 10x3)/20 = 3.0 — a tie
            # when the fit only realized its OWN side: build it exactly
            H_tie = [_mkweek("2026-W37", {"OUT": (1.0, 1.0)}),
                     {"week": "2026-W39", "generated_ts": "t",
                      "states": {"OUT": {
                          "in_force": {"fires": 10, "usd": 25.0,
                                       "usd_per_fire": 2.5},
                          "other": {"fires": 10, "usd": 25.0,
                                    "usd_per_fire": 2.5},
                          "other_src": "outcome-fitted"}}}]
            amv = amf(H_tie, [r["week"] for r in H_tie],
                      {"action": "flip",
                       "evidence": [{"week": "2026-W37", "fit_edge": -1.0}]},
                      "OUT")
            check("aftermath: a tie pays the decision (no false COST)",
                  amv["status"] == "graded"
                  and amv["realized"] == {"bar": "quantile", "fires": 10,
                                          "usd_per_fire": 2.5,
                                          "usd_total": 25.0}
                  and amv["counterfactual"]["usd_per_fire"] == 2.5
                  and amv["counterfactual"]["usd_total"] == 25.0
                  and amv["verdict"].startswith("the decision PAID"), amv)
            amc = amf(H_am, [r["week"] for r in H_am],
                      {"action": "flip",
                       "evidence": [{"week": "2026-W37", "fit_edge": -1.0}]},
                      "OUT")
            check("aftermath: after a FLIP the realized side is the "
                  "quantile (the decision's own bar), pooled over both "
                  "post-decision weeks",
                  amc["status"] == "graded"
                  and amc["realized"] == {"bar": "quantile", "fires": 20,
                                          "usd_per_fire": 2.0,
                                          "usd_total": 40.0}
                  and amc["counterfactual"] == {"bar": "fit", "fires": 20,
                                                "usd_per_fire": 3.0,
                                                "usd_total": 60.0}
                  and amc["weeks"] == 2
                  and amc["verdict"].startswith("the decision COST"), amc)
            amb = amf([_mkweek("2026-W37", {"OUT": (3.0, 2.0)}),
                       _mkweek("2026-W39", {"OUT": (3.0, 2.0)}),
                       _mkweek("2026-W40", {"OUT": (3.0, 2.0)})],
                      [r["week"] for r in H_am],
                      {"action": "flip_back",
                       "evidence": [{"week": "2026-W37", "fit_edge": -2.0}]},
                      "OUT")
            check("aftermath: after a FLIP-BACK the realized side is the "
                  "fit (in_force — the seats are regime-normalized by "
                  "each record's own other_src)",
                  amb["realized"]["bar"] == "fit"
                  and amb["realized"]["usd_per_fire"] == 3.0
                  and amb["counterfactual"]["bar"] == "quantile", amb)
            check("aftermath: pending with no post-decision weeks",
                  amf(H_am, [r["week"] for r in H_am],
                      {"action": "flip", "evidence": [{"week": "2026-W40"}]},
                      "OUT")["status"] == "pending")
            H_one = [_mkweek("2026-W37", {"OUT": (1.0, 1.0)}),
                     {"week": "2026-W41", "generated_ts": "t",
                      "states": {"OUT": {"in_force": {"fires": 10, "usd": 30.0,
                                                     "usd_per_fire": 3.0},
                                        "other": {"fires": 0},
                                        "other_src": "quantile"}}}]
            check("aftermath: a one-sided counterfactual stays pending "
                  "(no fake zero-rate)",
                  amf(H_one, [r["week"] for r in H_one],
                      {"action": "flip",
                       "evidence": [{"week": "2026-W37"}]},
                      "OUT")["status"] == "pending")
            # the GOVERNED WINDOW: the flip's weeks end after the next
            # event's newest evidence week (the quantile still shipped
            # during the flip_back's evidence); W41 is fit-live again
            # and must NOT pool into the flip's aftermath
            H_win = [_mkweek("2026-W37", {"OUT": (1.0, 1.0)}),
                     _mkweek("2026-W39", {"OUT": (2.0, 3.0)},
                             src="outcome-fitted"),
                     _mkweek("2026-W40", {"OUT": (10.0, 1.0)},
                             src="outcome-fitted"),
                     _mkweek("2026-W41", {"OUT": (4.0, 1.0)})]
            amw = amf(H_win, [r["week"] for r in H_win],
                      {"action": "flip", "evidence": [{"week": "2026-W37"}]},
                      "OUT",
                      next_ev={"action": "flip_back",
                               "evidence": [{"week": "2026-W40"}]})
            check("aftermath: the governed window closes where the next "
                  "event takes over (no mixed-regime pooling beyond it)",
                  amw["status"] == "graded" and amw["weeks"] == 2
                  and amw["realized"] == {"bar": "quantile", "fires": 20,
                                          "usd_per_fire": 6.0,
                                          "usd_total": 120.0}
                  and amw["verdict"].startswith("the decision PAID"), amw)
            tbw.HISTORY_FILE = real_hist2
        finally:
            tbw.HISTORY_FILE = real_hist2
            Hg_real.unlink(missing_ok=True)
    finally:
        tbw.FLIPGRADE_FILE = real_fg
        tbw.FLIP_EVENTS_FILE = real_ev_fg
        tbw.FLIPGRADE_JSON = real_fgj
        tbw.FLIP_FILE = real_flipf
        tbw.RECKONING_WINDOW_FILE = real_rkwin
    H1 = [_mkweek("2026-W39", {"OUT": (1.0, 2.5)})]
    evs4, _ = tbw._evaluate_flips(H1, {})
    check("one losing week never flips (the streak guard)",
          evs4["OUT"] is None, evs4)
    H2 = [_mkweek(f"2026-W{i}", {"OUT": (3.0, 1.0)}) for i in (36, 37, 38)] \
        + [_mkweek("2026-W39", {"OUT": (1.0, 3.0)})]
    evs5, _ = tbw._evaluate_flips(H2, {})
    check("a losing streak with a POSITIVE pool never flips (the pool guard)",
          evs5["OUT"] is None, evs5)
    Hx = [_mkweek("2026-W37", {"OUT": (1.0, 2.0)}),
          _mkweek("2026-W38", {}),                      # one-sided: no counterfactual
          _mkweek("2026-W39", {"OUT": (1.0, 2.5)})]
    evs6, _ = tbw._evaluate_flips(Hx, {})
    check("a one-sided week breaks the streak (honest, not lucky)",
          evs6["OUT"] is None, evs6)
    roll = tbw._rolling_grade(H, "OUT")
    check("the rolling grade pools on the canonical fit-vs-quantile axis",
          roll["weeks"] == 4 and roll["fit"]["fires"] == 40
          and roll["quantile"]["fires"] == 40
          and abs(roll["delta"] - ((2.0 + 1.0 + 1.0 + 1.0) / 4
                                   - (1.0 + 1.5 + 2.0 + 2.5) / 4)) < 0.02, roll)
    roll_mix = tbw._rolling_grade([Wa, Wb], "OUT")
    check("the pooled fit edge is regime-normalized across a flip",
          abs(roll_mix["delta"] - (-2.0)) < 1e-9, roll_mix)
    check("the week dedupe prevents same-week double-append (the boot-restart race)",
          all(r.get("week") for r in H) and len(set(r["week"] for r in H)) == len(H))

    # the fail-safes: a broken store and a thin week keep the report
    # (the baseline is the marks block's last write — that block ran a
    # fresh report_now() of its own)
    n_before = txt_before_failsafe
    tl.learn = lambda *a, **k: {"err": "store broke", "states": {}}
    s2 = tbw.report_now()
    check("a broken store -> err summary, the report stands",
          "store broke" in s2.get("err", "")
          and len(tbw.OUT_FILE.read_text(encoding="utf-8")) == n_before)
    tbw._week_candidates = lambda: (None, "no candidates this week")
    tl.learn = lambda *a, **k: MODEL
    s3 = tbw.report_now()
    check("a thin week -> err summary, the report stands",
          "no candidates" in s3.get("err", "")
          and len(tbw.OUT_FILE.read_text(encoding="utf-8")) == n_before)

    # the all-tides quantile fallback: one-sided ONLY when the score
    # history itself is gone
    real_qbar = tbw._quantile_bar_as_learn
    tbw._quantile_bar_as_learn = lambda st, m: None
    tbw._week_candidates = lambda: ([("OUT", 85.0, True)], None)
    tbw.OUT_FILE.write_text("", encoding="utf-8")
    tbw.report_now()
    txt_fb = tbw.OUT_FILE.read_text(encoding="utf-8")
    check("a thin tide-specific quantile falls back to the all-tides "
          "quantile (still a counterfactual)",
          "quantile (all-tides)" in txt_fb, txt_fb)
    _tl2._funnel_score_quantiles = lambda: None
    tbw.report_now()
    txt_none = tbw.OUT_FILE.read_text(encoding="utf-8")
    check("no score history at all -> honestly one-sided",
          "not available" in txt_none, txt_none)
    tbw._quantile_bar_as_learn = real_qbar

    # ── 5. prepend + rotate ──────────────────────────────────────────
    tbw.OUT_FILE.write_text("OLD SECTION\n", encoding="utf-8")
    tbw._week_candidates = lambda: ([("OUT", 85.0, True)], None)
    tbw.report_now()
    txt2 = tbw.OUT_FILE.read_text(encoding="utf-8")
    check("the newest section is at the top",
          txt2.startswith("=== THE TIDE BAR WEEKLY")
          and "OLD SECTION" in txt2)
    check("rotation ceiling is 500 KB",
          tbw.REPORT_MAX_BYTES == 500_000)
finally:
    tbw.OUT_FILE = real_out
    tbw.SNAP_OUTCOMES_FILE = real_snap
    tbw.TRADES_FILE = real_trades
    tbw.HISTORY_FILE = real_hist
    tbw.FLIP_FILE = real_flip
    tbw.JSON_FILE = real_json
    tbw.FLIP_EVENTS_FILE = real_events
    tbw.POOLMIN_LEARN_FILE = real_poolmin
    tl.learn = real_learn
    tbw._week_candidates = real_week
    try:
        import tide_learn as _tlf
        _tlf._outcome_fit_bar = real_fit
        _tlf._funnel_score_quantiles = real_q
    except Exception:
        pass
    tl._cache.clear()
    tl._cache.update(real_cache)
    import shutil
    shutil.rmtree(sandbox, ignore_errors=True)

# the production report was never touched by a test: the byte snapshot
# taken before the sandbox block must be UNCHANGED after it
check("the production report was never written by a test (byte-stable)",
      _out_bytes() == _prod_out_bytes)

# ── 7. THE BENCH'S FINAL VERDICT (Sep 12): a full report_now() bench
#    arc — the flip-back EVENT RECORD snapshots the bench's OPEN live
#    verdict at the moment the bench ended. The arc (date-independent:
#    the flip-back lands on the pass's own ISO week):
#    W32-34 the fit bleeds while in force (fit $1.00/fire vs the
#    quantile's $3.00), the hand-persisted real flip at W34 benches
#    OUT; W35-36 the quantile ships but the fit's counterfactual
#    out-earns it (the bench COSTS); the pass stages its own winning
#    week and the gate flips OUT back — the record carries bench_final
#    graded on the OPEN window (W35..now): to_date -$60.00.
import tide_learn as _tbl

_saved_bf = {k: getattr(tbw, k) for k in
             ("OUT_FILE", "JSON_FILE", "SNAP_OUTCOMES_FILE", "TRADES_FILE",
              "HISTORY_FILE", "FLIP_FILE", "FLIPGRADE_FILE",
              "FLIPGRADE_JSON", "FLIP_EVENTS_FILE")}
real_cand_bf = tbw._week_candidates
real_learn_bf = tl.learn
real_grade_bf = tbw._grade
real_q_bf = _tbl._funnel_score_quantiles
real_fitbar_bf = _tbl._outcome_fit_bar
try:
    # the path re-points INSIDE the try (the PULSE_JSON pattern): the
    # finally below restores the sandbox even on a mid-suite crash
    _bf_sbx = sandbox / "benchfinal"
    _bf_sbx.mkdir(parents=True, exist_ok=True)
    tbw.OUT_FILE = _bf_sbx / "weekly.txt"
    tbw.JSON_FILE = _bf_sbx / "sidecar.json"
    tbw.SNAP_OUTCOMES_FILE = _bf_sbx / "snap.jsonl"
    tbw.TRADES_FILE = _bf_sbx / "trades.jsonl"
    tbw.HISTORY_FILE = _bf_sbx / "history.jsonl"
    tbw.FLIP_FILE = _bf_sbx / "flips.json"
    tbw.FLIPGRADE_FILE = _bf_sbx / "fg.txt"
    tbw.FLIPGRADE_JSON = _bf_sbx / "fg.json"
    tbw.FLIP_EVENTS_FILE = _bf_sbx / "events.jsonl"
    # deterministic dollars: the LOW bar (86, the quantile) realizes
    # $1.00/fire, the HIGH bar (92, the refit) $3.00/fire — so the
    # staged week's fit edge is +2.00 in either regime.
    tbw._grade = lambda cands, bar: (
        None if bar is None else
        {"bar": bar, "fires": 10, "payers": 1, "rate": 0.1,
         "usd": 10.0 if bar < 90 else 30.0,
         "usd_per_fire": 1.0 if bar < 90 else 3.0})
    tbw._week_candidates = lambda: ([
        ("OUT", 88.0, True)] * 10 + [
        ("OUT", 93.0, True)] * 10 + [
        ("FLAT", 88.0, True)], None)
    # the stub models the REAL learner's flips-awareness: a benched
    # state ships the QUANTILE (bar 86), not the MODEL's bar_src.
    def _learn_stub_bf(*a, **k):
        m = dict(MODEL)
        if ((tbw._load_flips() or {}).get("OUT") or {}).get("flipped"):
            m["states"]["OUT"] = dict(m["states"]["OUT"],
                                      bar=86.0, bar_src="quantile")
        return m
    tl.learn = _learn_stub_bf
    _tbl._funnel_score_quantiles = lambda: [50.0] * 60
    # the fit's counterfactual bar: 92 (the $3.00 seat) — the real fit
    # bar on constant-50 scores would sit at 80 and collapse the edge
    _tbl._outcome_fit_bar = lambda st, sc, arr_cache=None: (
        {"bar": 92.0, "ev_o": 3.0, "ev_q": 1.0, "n": 100,
         "payer_rate": 0.1, "state": st} if st == "OUT" else
        {"bar": 92.0, "ev_o": 2.0, "ev_q": 2.0, "n": 100,
         "payer_rate": 0.105, "state": st})

    def _bf_rec(wk, inf_pf, oth_pf, src):
        return {"week": wk, "generated_ts": "t", "states": {"OUT": {
            "in_force": {"fires": 10, "payers": 1,
                         "usd": round(10 * inf_pf, 2),
                         "usd_per_fire": round(inf_pf, 2)},
            "other": {"fires": 10, "payers": 1,
                      "usd": round(10 * oth_pf, 2),
                      "usd_per_fire": round(oth_pf, 2)},
            "other_src": src}}}

    # the seed: W32-34 the fit bleeds (in force, un-benched), W35-36
    # the bench's weeks (the quantile ships; the fit's cf out-earns),
    # and the hand-persisted real flip at W34.
    tbw.HISTORY_FILE.write_text("".join(
        json.dumps(_bf_rec(f"2026-W{n}", 1.0, 3.0,
                           "quantile" if n <= 34 else "outcome-fitted"))
        + "\n" for n in (32, 33, 34, 35, 36)), encoding="utf-8")
    tbw.FLIP_FILE.write_text(json.dumps(
        {"OUT": {"flipped": True, "since": "t"},
         "FLAT": {"flipped": False}, "RISING": {"flipped": False}}),
        encoding="utf-8")
    tbw._persist_flip_events(
        {"OUT": {"action": "flip", "losing_weeks": 3, "roll_delta": -2.0,
                 "evidence": [{"week": f"2026-W{n}", "fit_edge": -2.0}
                              for n in (32, 33, 34)]}},
        "2026-W34", "t")
    check("the seed persisted exactly one real flip event",
          len(tbw._load_flip_events()) == 1
          and not tbw._load_flip_events()[0].get("sim"))

    tbw.report_now()   # stages its own week (a winning one); flips back

    evs_bf = tbw._load_flip_events()
    fb_bf = next((e for e in evs_bf if e.get("action") == "flip_back"),
                 None)
    check("the gate flipped OUT back on the pass's own ISO week (a real "
          "event, not sim)",
          fb_bf is not None and fb_bf.get("week", "").startswith("2026-W")
          and not fb_bf.get("sim"), evs_bf)

    bf_bf = (fb_bf or {}).get("bench_final")
    check("the flip-back record carries the bench's FINAL verdict",
          isinstance(bf_bf, dict) and bf_bf.get("verdict") == "COSTING",
          fb_bf)
    # OPEN window: the flip's newest evidence week is W34, so the open
    # aftermath reads W35..now: realized (the quantile) 3x$10 = $30,
    # counterfactual (the fit) 3x$30 = $90 -> to_date -$60.00.
    check("the snapshot graded the OPEN window (to_date -$60.00 over "
          "3 weeks)",
          bf_bf.get("to_date") == -60.0 and bf_bf.get("weeks") == 3, bf_bf)
    check("the text carries the cumulative clause",
          "to date the bench is down $60.00 cumulative"
          in (bf_bf.get("text") or ""), bf_bf)

    n_evs_bf = len(tbw._load_flip_events())
    tbw.report_now()
    check("re-running the pass cannot double-persist the flip-back (the "
          "snapshot rides the dedupe)",
          len(tbw._load_flip_events()) == n_evs_bf,
          (n_evs_bf, len(tbw._load_flip_events())))

    # a flip-back WITHOUT a graded open window ships no fabricated verdict
    ev2_bf = {"FLAT": {"action": "flip_back", "winning_weeks": 2,
                       "roll_delta": 1.0,
                       "evidence": [{"week": "2026-W39", "fit_edge": 1.0}]}}
    tbw._attach_bench_final(ev2_bf, [])
    check("no flip on the trail / thin ledger -> no snapshot, never fake",
          "bench_final" not in ev2_bf["FLAT"], ev2_bf)
finally:
    for k, v in _saved_bf.items():
        setattr(tbw, k, v)
    tbw._week_candidates = real_cand_bf
    tl.learn = real_learn_bf
    tbw._grade = real_grade_bf
    _tbl._funnel_score_quantiles = real_q_bf
    _tbl._outcome_fit_bar = real_fitbar_bf
    import shutil as _shutil_bf
    _shutil_bf.rmtree(_bf_sbx, ignore_errors=True)

# ── THE BORROW'S RECKONING (Sep 12): every real flip-back's verdict was
#    borrowed while pending (at closure the state is un-benched ->
#    COSTING); once its aftermath grades, the grader compares the
#    borrowed tag with the realized dollars and WARNs either way. Own
#    sandbox: trail, history, the logger's handlers. ──────────────────
_saved_rk = {k: getattr(tbw, k)
             for k in ("FLIP_EVENTS_FILE", "HISTORY_FILE")}
_rk_sbx = BASE / "_rk_sbx"
_rk_sbx.mkdir(exist_ok=True)
tbw.FLIP_EVENTS_FILE = _rk_sbx / "events.jsonl"
tbw.HISTORY_FILE = _rk_sbx / "history.jsonl"
import logging as _logging_rk

class _RkCapture(_logging_rk.Handler):
    """A real logging.Handler: the logger's call goes through
    callHandlers, which reads hdlr.level — a bare duck-typed object
    with only a warning() breaks the emit chain (and the hook's
    fail-open would swallow the error, which is exactly the lesson)."""
    def __init__(self):
        super().__init__(level=0)
        self.msgs = []

    def emit(self, record):
        self.msgs.append(record.getMessage())

_rk_logger = _logging_rk.getLogger("tidebar-weekly")
_rk_cap = _RkCapture()
_rk_logger.addHandler(_rk_cap)
try:
    def _rk_rec(wk, inf_pf, oth_pf, src):
        return {"week": wk, "generated_ts": "t", "states": {"OUT": {
            "in_force": {"fires": 10, "payers": 3,
                         "usd": round(10 * inf_pf, 2),
                         "usd_per_fire": round(inf_pf, 2)},
            "other": {"fires": 10, "payers": 3,
                      "usd": round(10 * oth_pf, 2),
                      "usd_per_fire": round(oth_pf, 2)},
            "other_src": src}}}

    # the arc: flip@W39, W40-41 the bench (quantile $3.00 vs fit $1.00),
    # flip_back@W41, W42 the re-armed fit bleeding ($1.00 vs $3.00)
    H_rk = [_rk_rec(f"2026-W{n}", 1.0, 3.0 - (n - 36), "quantile")
            for n in (36, 37, 38, 39)]
    H_rk += [_rk_rec("2026-W40", 3.0, 1.0, "outcome-fitted"),
             _rk_rec("2026-W41", 3.0, 1.0, "outcome-fitted")]
    tbw.FLIP_EVENTS_FILE.write_text(
        json.dumps({"action": "flip", "state": "OUT", "week": "2026-W39",
                    "ts": "t", "evidence": [
                        {"week": f"2026-W{n}", "fit_edge": -2.0}
                        for n in (37, 38, 39)]}) + "\n"
        + json.dumps({"action": "flip_back", "state": "OUT",
                      "week": "2026-W41", "ts": "t",
                      "winning_weeks": 2, "roll_delta": 0.5,
                      "evidence": [{"week": "2026-W40", "fit_edge": 0.5},
                                   {"week": "2026-W41",
                                    "fit_edge": 0.6}]}) + "\n",
        encoding="utf-8")

    check("the still-pending flip-back is not stamped and nothing warns",
          tbw._attach_borrow_reckoning(H_rk) == [] and _rk_cap.msgs == [],
          (_rk_cap.msgs,))
    check("re-running the pending pass stays quiet (no double stamps)",
          tbw._attach_borrow_reckoning(H_rk) == [] and _rk_cap.msgs == [])

    # the closure stamp: the flip-back persists at its closure pass with
    # no reckoning yet — _persist's dedupe must not erase the later
    # stamp riding the same (state, action, week) key
    tbw._persist_flip_events(
        {"OUT": {"action": "flip_back", "winning_weeks": 2,
                 "roll_delta": 0.5,
                 "evidence": [{"week": "2026-W40", "fit_edge": 0.5},
                              {"week": "2026-W41", "fit_edge": 0.6}]}},
        "2026-W41", "t-closure")
    st1 = tbw._attach_borrow_reckoning(
        H_rk + [_rk_rec("2026-W42", 1.0, 3.0, "quantile")])
    fb_rk = next(e for e in tbw._load_flip_events()
                 if e.get("action") == "flip_back")
    check("the closure stamp survives _persist's dedupe (the later pass "
          "stamps the same flip-back)",
          st1 == ["OUT"] and isinstance(fb_rk.get("borrow_reckoning"),
                                        dict), (st1, fb_rk))
    check("the agreement WARNS once — the borrow told the truth",
          len(_rk_cap.msgs) == 1 and "AGREES" in _rk_cap.msgs[0]
          and "COSTING" in _rk_cap.msgs[0], _rk_cap.msgs)
    check("an already-stamped event never re-warns (idempotent)",
          tbw._attach_borrow_reckoning(
              H_rk + [_rk_rec("2026-W42", 1.0, 3.0, "quantile")]) == []
          and len(_rk_cap.msgs) == 1)

    # the disagreement: same trail, the re-arm PAYS (the borrow was
    # wrong) — the WARN carries the dollars clause, the stamp rides the
    # event, and the pure grader returns the gap
    _rk_cap.msgs.clear()
    tbw.FLIP_EVENTS_FILE.write_text(
        json.dumps({"action": "flip", "state": "OUT", "week": "2026-W39",
                    "ts": "t", "evidence": [
                        {"week": f"2026-W{n}", "fit_edge": -2.0}
                        for n in (37, 38, 39)]}) + "\n"
        + json.dumps({"action": "flip_back", "state": "OUT",
                      "week": "2026-W41", "ts": "t",
                      "winning_weeks": 2, "roll_delta": 0.5,
                      "evidence": [{"week": "2026-W40", "fit_edge": 0.5},
                                   {"week": "2026-W41",
                                    "fit_edge": 0.6}]}) + "\n",
        encoding="utf-8")
    H_dis = H_rk[:6] + [_rk_rec("2026-W42", 3.0, 1.0, "quantile")]
    st3 = tbw._attach_borrow_reckoning(H_dis)
    fb_dis = next(e for e in tbw._load_flip_events()
                  if e.get("action") == "flip_back")
    check("a wrong borrow is stamped DISAGREE and WARNs with the gap "
          "(realized $3.00 vs counterfactual $1.00 = +$2.00/fire)",
          st3 == ["OUT"]
          and fb_dis["borrow_reckoning"]["status"] == "disagree"
          and fb_dis["borrow_reckoning"]["gap_per_fire"] == 2.0
          and any("DISAGREES" in m and "$+2.00/fire" in m
                  for m in _rk_cap.msgs), (st3, _rk_cap.msgs))
    rk_pure = tbw.borrow_reckoning(fb_dis, "OUT", tbw._event_aftermath(
        H_dis, [r["week"] for r in H_dis], fb_dis, "OUT"))
    check("the pure grader returns the disagreement's dollars clause",
          rk_pure["status"] == "disagree" and rk_pure["borrowed"] == "COSTING"
          and rk_pure["gap_per_fire"] == 2.0
          and "read backwards" in rk_pure["text"], rk_pure)

    # a sim flip-back never enters the reckoning — the replay is not a
    # lived decision, even with a graded sim_aftermath attached
    tbw.FLIP_EVENTS_FILE.write_text(
        json.dumps({"action": "flip_back", "state": "OUT",
                    "week": "2026-W41", "ts": "t", "sim": True,
                    "sim_aftermath": {
                        "status": "graded", "weeks": 1,
                        "realized": {"bar": "fit", "fires": 10,
                                     "usd_per_fire": 3.0},
                        "counterfactual": {"bar": "quantile", "fires": 10,
                                           "usd_per_fire": 1.0},
                        "verdict": "the decision PAID"},
                    "evidence": []}) + "\n", encoding="utf-8")
    check("a sim flip-back never enters the reckoning",
          tbw._attach_borrow_reckoning(H_dis) == []
          and len(_rk_cap.msgs) == 1, _rk_cap.msgs)
finally:
    for k, v in _saved_rk.items():
        setattr(tbw, k, v)
    _rk_logger.removeHandler(_rk_cap)
    import shutil as _shutil_rk
    _shutil_rk.rmtree(_rk_sbx, ignore_errors=True)

# ── THE POOL-GATE DIAL GRADE (Sep 12): grade_poolgate judges the
#    shipped POOL_MIN on the REAL ledger each monthly pass — which
#    graduated thresholds would have denied which real re-arms, in
#    dollars (denied COSTING saves, denied PAYING misses, DELAYED
#    banks only to the delayed arm) — riding grade_ledger's txt and
#    summary. Hermetic: the loaders and the grade file are sandboxed. ──
import json as _json_pg
import tempfile as _tempfile_pg
_pg_sbx = Path(_tempfile_pg.mkdtemp())
_pg_saved = {k: getattr(tbw, k) for k in
             ("HISTORY_FILE", "FLIP_EVENTS_FILE", "FLIPGRADE_FILE",
              "FLIPGRADE_JSON", "FLIP_FILE", "RECKONING_WINDOW_FILE")}
try:
    tbw.HISTORY_FILE = _pg_sbx / "h.jsonl"
    tbw.FLIP_EVENTS_FILE = _pg_sbx / "evs.jsonl"
    tbw.FLIPGRADE_FILE = _pg_sbx / "fg.txt"
    tbw.FLIPGRADE_JSON = _pg_sbx / "fg.json"
    tbw.FLIP_FILE = _pg_sbx / "flips.json"
    tbw.RECKONING_WINDOW_FILE = _pg_sbx / "rkwin.jsonl"

    def _pgmk(tag, di, do, src):
        return {"week": tag, "generated_ts": tag,
                "states": {"FLAT": {
                    "in_force": {"fires": 10, "payers": 3, "rate": 0.3,
                                 "usd": 10 * di, "usd_per_fire": di},
                    "other": {"fires": 10, "payers": 3, "rate": 0.3,
                              "usd": 10 * do, "usd_per_fire": do},
                    "other_src": src}}}

    # honest seed: benched W40 (fit cf 2.2 vs quantile 2.0), re-arm W41
    # (fit 2.4 vs 2.0) — the gate's own read at W41: rd = 2.3 - 2.0 =
    # +0.30, so +0.25 passes and +0.50 denies; the decision-time stamp
    # carries both dials' inputs (pool 0.30, streak 2 winning weeks)
    pg_ev = {"state": "FLAT", "action": "flip_back", "week": "2026-W41",
             "roll_delta": 0.3, "winning_weeks": 2,
             "evidence": [{"week": "2026-W40", "fit_edge": 0.2},
                          {"week": "2026-W41", "fit_edge": 0.4}]}
    pg_cost = [_pgmk("2026-W40", 2.0, 2.2, "outcome-fitted"),
               _pgmk("2026-W41", 2.4, 2.0, "quantile"),
               _pgmk("2026-W42", 1.0, 3.0, "quantile"),
               _pgmk("2026-W43", 1.0, 3.0, "quantile")]
    pg_pay = [_pgmk("2026-W40", 2.0, 2.2, "outcome-fitted"),
              _pgmk("2026-W41", 2.4, 2.0, "quantile"),
              _pgmk("2026-W42", 2.4, 2.0, "quantile"),
              _pgmk("2026-W43", 5.0, 2.0, "quantile")]

    pg1 = tbw.grade_poolgate(pg_cost, [pg_ev])
    _t25, _t50 = pg1["thresholds"][0.25], pg1["thresholds"][0.50]
    check("pool-gate grade: the pool below +0.25 passes both dials; "
          "+0.50 DENIES the costing re-arm and prices the save in "
          "the trail's own dollars ($2.00/fire x 20 fires = $40.00)",
          _t25 == {"denied": 0, "delayed": 0, "net": 0.0, "events": []}
          and _t50["denied"] == 1 and _t50["net"] == 40.0, (_t25, _t50))
    pg2 = tbw.grade_poolgate(pg_pay, [pg_ev])
    _t50b = pg2["thresholds"][0.50]
    check("pool-gate grade: when the denied gate re-arms later, the "
          "dial DELAYS (banks only to the delayed arm) and a PAYING "
          "window reports the missed money as negative ($-4.00)",
          _t50b["delayed"] == 1
          and _t50b["events"][0]["delay_week"] == "2026-W43"
          and _t50b["net"] == -4.0, _t50b)
    pg3 = tbw.grade_poolgate(pg_cost, [])
    check("pool-gate grade: no real flip-backs grades nothing, "
          "honestly (the sims never enter), and the shipped "
          "POOL_MIN is never disturbed",
          any("no real flip-backs" in l for l in pg3["lines"])
          and tbw.POOL_MIN == 0.0, pg3["lines"])
    # ── THE STREAK DIAL (Sep 12): the same event's decision-time
    #    winning_weeks against the graduated FBW candidates — the
    #    sweep's other axis on live data, priced in the trail's own
    #    dollars by the SAME walk machine ──
    _s1 = pg1["streak"]
    check("pool-gate grade: the STREAK dial denies the 2-week re-arm "
          "at FBW=3 and FBW=4 — same dollars as the pool denial "
          "($40.00 each, one shared governed window)",
          _s1[3]["denied"] == 1 and _s1[3]["net"] == 40.0
          and _s1[4]["denied"] == 1 and _s1[4]["net"] == 40.0,
          (str(_s1), [l for l in pg1["lines"] if "streak" in l]))
    _s2 = pg2["streak"]
    check("pool-gate grade: on the paying stream the streak dial "
          "DELAYS — FBW=3's walk counts the flip-back's own evidence "
          "weeks too (the gate's real streak semantics), so its first "
          "legal re-arm is W42 banking $0.00; FBW=4 arms at W43 and "
          "banks the same -$4.00 one-bad-week span the pool dial did",
          _s2[3]["delayed"] == 1 and _s2[3]["events"][0]["delay_week"]
          == "2026-W42" and _s2[3]["net"] == 0.0
          and _s2[4]["delayed"] == 1 and _s2[4]["events"][0]["delay_week"]
          == "2026-W43" and _s2[4]["net"] == -4.0,
          (str(_s2), [l for l in pg2["lines"] if "streak" in l]))
    pg4 = tbw.grade_poolgate(
        pg_cost, [{"state": "FLAT", "action": "flip_back",
                   "week": "2026-W41"}])
    check("pool-gate grade: a real flip-back without its decision-time "
          "stamp grades nothing for BOTH dials, said honestly (a "
          "pre-stamp record; every fire has carried the stamp since "
          "Sep 12) — never a silent skip",
          any("no decision-time stamp" in l for l in pg4["lines"])
          and all(v["denied"] == 0 and v["delayed"] == 0
                  for v in pg4["streak"].values())
          and all(v["denied"] == 0 and v["delayed"] == 0
                  for v in pg4["thresholds"].values()),
          pg4["lines"])
    check("pool-gate grade: both dials' pins restore exactly (the "
          "shared walk mutates two globals, both in finally)",
          tbw.POOL_MIN == 0.0 and tbw.FLIP_BACK_WEEKS == 2,
          (tbw.POOL_MIN, tbw.FLIP_BACK_WEEKS))
    # ── THE BORROW'S EVIDENCE (Sep 12): reckoning stamps ride the
    #    dial grade — DENY/DELAY lines name how the borrow graded, the
    #    event dicts carry it, and clustered disagreements become
    #    evidence AGAINST the dials whose OWN denials ride them.
    #    Genuine-disagree stream: the fit re-arms and OUT-earns, so
    #    the borrowed COSTING truly graded wrong. Canonical ledger
    #    shape: one record per week, all states inside, seats
    #    consistent with the regime. ──
    _rk_recs = []
    for _w, _di, _do in (("2026-W40", 2.0, 2.2), ("2026-W41", 2.4, 2.0),
                         ("2026-W42", 3.0, 1.0), ("2026-W43", 3.0, 1.0)):
        _rk_recs.append({
            "week": _w, "generated_ts": _w, "states": {
                _st: {"in_force": {"fires": 10, "payers": 3,
                                   "rate": 0.3, "usd": 10 * _di,
                                   "usd_per_fire": _di},
                      "other": {"fires": 10, "payers": 3,
                                "rate": 0.3, "usd": 10 * _do,
                                "usd_per_fire": _do},
                      "other_src": ("outcome-fitted" if _w == "2026-W40"
                                    else "quantile")}
                for _st in ("OUT", "FLAT", "RISING")}})
    _rk_evs = [{"state": _st, "action": "flip_back",
                "week": "2026-W41", "roll_delta": 0.3,
                "winning_weeks": 2,
                "borrow_reckoning": {"status": "disagree"},
                "evidence": [{"week": "2026-W40", "fit_edge": 0.2},
                             {"week": "2026-W41", "fit_edge": 0.4}]}
               for _st in ("OUT", "FLAT", "RISING")]
    # the _pg sandbox redirects RECKONING_WINDOW_FILE, so these direct
    # calls evaluate the CUMULATIVE fallback deterministically — no
    # production window may leak its regime into a pin
    _rkg = tbw.grade_poolgate(_rk_recs, _rk_evs)
    _rk_lines = _rkg["lines"]
    check("pool-gate grade: the borrow's evidence rides the dial — a "
          "disagree-stamped denial names it on its line and its event "
          "dict, and the reckoning summary travels (empty window: the "
          "cumulative fallback)",
          any(l.rstrip().endswith("reckoning: the borrow disagreed")
              for l in _rk_lines)
          and all(ev.get("reckoning") == "disagree"
                  for ev in _rkg["thresholds"][0.50]["events"])
          and _rkg["reckoning"] == {"graded": 3, "agree": 0,
                                    "disagree": 3, "cluster": True,
                                    "window": []},
          [l for l in _rk_lines if "DENIES" in l or "DELAYED" in l][:3])
    check("pool-gate grade: clustered disagreements become per-dial "
          "EVIDENCE, named for the dials whose own denials ride them "
          "(never an auto-move — the dials turn by her hand)",
          sum(1 for l in _rk_lines if "CLUSTERED DISAGREEMENTS" in l) == 2
          and any("evidence against POOL_MIN=+0.50" in l
                  for l in _rk_lines)
          and any("evidence against FBW=3+4" in l for l in _rk_lines),
          [l for l in _rk_lines if "CLUSTERED" in l or "BORROW" in l])
    _rka = tbw.grade_poolgate(
        _rk_recs, [dict(e, borrow_reckoning={"status": "agree"})
                   for e in _rk_evs])
    check("pool-gate grade: the cluster control — agrees never tar a "
          "dial (no CLUSTERED line, cluster False)",
          not any("CLUSTERED" in l for l in _rka["lines"])
          and _rka["reckoning"]["cluster"] is False,
          _rka["reckoning"])
    check("pool-gate grade: an unstamped event prices identically but "
          "leaves no reckoning trace (pg1, above, has no stamp)",
          all("reckoning" not in ev
              for ev in pg1["thresholds"][0.50]["events"])
          and pg1["reckoning"]["graded"] == 0,
          pg1["reckoning"])
    check("pool-gate grade: the no-flip-backs path carries the same "
          "reckoning shape (uniform contract for grade_ledger)",
          pg3.get("reckoning") == {"graded": 0, "agree": 0,
                                   "disagree": 0, "cluster": False,
                                   "window": []}
          and any("THE BORROW'S RECORD" in l for l in pg3["lines"]),
          pg3.get("reckoning"))
    for r in pg_cost:
        tbw.HISTORY_FILE.open("a", encoding="utf-8").write(
            _json_pg.dumps(r) + "\n")
    tbw.FLIP_EVENTS_FILE.open("a", encoding="utf-8").write(
        _json_pg.dumps(dict(pg_ev, ts="t")) + "\n")
    tbw.FLIP_EVENTS_FILE.open("a", encoding="utf-8").write(
        _json_pg.dumps({"sim": True, "sim_source": "seeded replay",
                        "state": "FLAT", "action": "flip_back",
                        "week": "2026-W50", "roll_delta": -1.0,
                        "evidence": []}) + "\n")
    pg_sum = tbw.grade_ledger()
    pg_txt = tbw.FLIPGRADE_FILE.read_text(encoding="utf-8")
    pg_dial = pg_txt[pg_txt.index("THE POOL-GATE DIAL"):] \
        if "THE POOL-GATE DIAL" in pg_txt else ""
    check("the monthly grade embeds the dial: the POOL-GATE section "
          "renders in _flipgrade.txt, the summary carries the "
          "thresholds AND the streak dial, the STREAK VERDICT line "
          "renders, and the sim event stays excluded",
          "0.50 DENIES" in pg_dial and "poolgate" in pg_sum
          and "poolgate_streak" in pg_sum
          and "THE STREAK VERDICT" in pg_dial
          and "FBW=3" in pg_dial
          and "2026-W50" not in pg_dial,
          (bool(pg_dial), str(pg_sum.get("poolgate_streak"))[:200]))
    check("the monthly grade carries the reckoning evidence in its "
          "summary sidecar (poolgate_reckoning), zero-graded on the "
          "unstamped fixture, with the window key present and empty "
          "(no completed grade has recorded before this one)",
          pg_sum.get("poolgate_reckoning") == {"graded": 0, "agree": 0,
                                               "disagree": 0,
                                               "cluster": False,
                                               "window": []},
          str(pg_sum.get("poolgate_reckoning")))
finally:
    for k, v in _pg_saved.items():
        setattr(tbw, k, v)
    import shutil as _shutil_pg
    _shutil_pg.rmtree(_pg_sbx, ignore_errors=True)

# ── THE POOL-GATE FAMILY SWEEP (Sep 13): the diag's walk, wired into
#    grade_ledger — the gate re-run per graduated threshold so delays
#    and cascades EMERGE from the replay, priced in the trail's own
#    dollars; the threshold-0 walk must reproduce the lived events
#    (the baseline check). The sweep itself is pure over its inputs —
#    only the grade_ledger embedding pin takes a sandbox. ──

# ── THE SHARED ARCS (Sep 13): diag_poolgate's sim arc streams are
#    THE fixture set — poolgate_arcs.py is the ONE set of records the
#    study and the live machines both price. These pins assert the
#    live grade and the live sweep reproduce the diag's canon on the
#    SAME streams the diag's hand-arithmetic canon was derived from:
#    one semantics, or the suite says which side moved. ──
import poolgate_arcs as _pga

for _arc in _pga.ARCS:
    _st, _stream, _canon = _arc["state"], _arc["stream"], _arc["canon"]
    # canon events + pool stamps from the LIVE shipped gate itself
    # (_evaluate_flips verbatim, unflipped seeds — the arcs' flips are
    # on-stream, not pre-history)
    _fl = {s: {"flipped": False}
           for s in ("OUT", "FLAT", "RISING")}
    _ship_evs, _stamps = [], {}
    for _i, _rec in enumerate(_stream):
        _ew, _fl = tbw._evaluate_flips(_stream[:_i + 1], _fl)
        for _s in ("OUT", "FLAT", "RISING"):
            _e = _ew.get(_s)
            if _e:
                _ship_evs.append({"action": _e["action"],
                                  "week": str(_rec["week"])})
                _stamps[str(_rec["week"])] = _e.get("roll_delta")
    check(f"shared arc {_arc['key']}: the LIVE shipped gate "
          f"reproduces the diag's canonical events "
          f"({', '.join(f'{a}@{w}' for a, w in _canon) or 'none'})",
          [(e["action"], e["week"]) for e in _ship_evs] == _canon,
          _ship_evs)
    for _w, _pool in _arc["pools"].items():
        check(f"shared arc {_arc['key']}: the gate's pool stamp at "
              f"{_w} matches the diag's hand arithmetic "
              f"({_pool:+.4f})",
              _stamps.get(_w) is not None
              and abs(_stamps[_w] - _pool) < 1e-6, _stamps.get(_w))
    # the walked family on the SAME stream == the diag's canon net
    _fs = tbw._poolgate_family_sweep(
        _stream, [{"state": _st, "action": a, "week": w}
                  for a, w in _canon])
    check(f"shared arc {_arc['key']}: the sweep's threshold-0 walk "
          f"reproduces the canon (baseline_ok) on the shared stream",
          _fs["baseline_ok"] is True, _fs["lines"][:3])
    if _arc["key"] == "arc1":
        check("shared arc 1: no re-arm, nothing to deny — every "
              "threshold prices identical governance (the sweep's "
              "encoding of the diag's honest None)",
              _fs["family"] == {0.25: 0.0, 0.50: 0.0}
              and all("identical governance" in l
                      for l in _fs["lines"] if l.startswith("  +0")),
              _fs["family"])
    for _pm, _canon_net in _pga.FAMILY_CANON[_arc["key"]].items():
        if _canon_net is None:
            continue   # arc 1: covered by the identical-governance pin
        _net = _fs["family"].get(_pm)
        check(f"shared arc {_arc['key']}: the LIVE family sweep at "
              f"{_pm:+.2f} prices the diag's canon "
              f"(${_canon_net:+,.2f}) on the same stream",
              _net is not None and abs(_net - _canon_net) < 0.01,
              (_net, _canon_net))
    # the stamp grade's DENY dollars are the event's own aftermath —
    # priced through the SAME span-savings lens as the walk now, but
    # over the RE-ARM's governed window (to the next event), not the
    # walk's stream-end divergence: for arc 3 that is W42-W43 =
    # $196.80 (the walk's $307.50 adds the W44 week the denial also
    # re-benches — the two-lens structure pinned on both sides)
    if _arc["key"] == "arc3":
        _ev3 = [{"state": _st, "action": "flip", "week": "2026-W39",
                 "roll_delta": -0.5, "winning_weeks": 3,
                 "evidence": [{"week": "2026-W37", "fit_edge": -0.40},
                              {"week": "2026-W38", "fit_edge": -0.60},
                              {"week": "2026-W39", "fit_edge": -0.85}]},
                {"state": _st, "action": "flip_back", "week": "2026-W41",
                 "roll_delta": 0.19, "winning_weeks": 2,
                 "evidence": [{"week": "2026-W40", "fit_edge": -1.15},
                              {"week": "2026-W41", "fit_edge": -1.05}]},
                {"state": _st, "action": "flip", "week": "2026-W44",
                 "roll_delta": -1.25, "winning_weeks": 3,
                 "evidence": [{"week": "2026-W42", "fit_edge": 1.25},
                              {"week": "2026-W43", "fit_edge": 1.15}]}]
        _pg3 = tbw.grade_poolgate(_stream, _ev3)
        for _pm in _pga.THRESHOLDS:
            _t = _pg3["thresholds"][_pm]
            check(f"shared arc 3: the STAMP grade at {_pm:+.2f} denies "
                  f"the bad re-arm at the event's own governed window "
                  f"lens (+$196.80 = the walk's $307.50 canon minus "
                  f"the W44 week the denial also re-benches)",
                  _t["denied"] == 1 and abs(_t["net"] - 196.80) < 0.01,
                  (_t.get("denied"), _t.get("net")))
    if _arc["key"] == "arc2":
        # the KNOWN, QUANTIFIED difference: the +0.50 denial DELAYS
        # the good re-arm to W45; the stamp grade prices the governed
        # window to the arm week (W44) = -$82.00, while the governance
        # walk also prices the arm-lag week (W45, the walked arm ships
        # from W46) = -$180.40 canon. Same machine, two lenses — the
        # pin holds both so neither side can drift silently.
        _pg2 = tbw.grade_poolgate(
            _stream,
            [{"state": _st, "action": "flip", "week": "2026-W39",
              "roll_delta": -0.5, "winning_weeks": 3,
              "evidence": [{"week": "2026-W37", "fit_edge": -0.40},
                           {"week": "2026-W38", "fit_edge": -0.70},
                           {"week": "2026-W39", "fit_edge": -0.90}]},
             {"state": _st, "action": "flip_back", "week": "2026-W43",
              "roll_delta": 0.47, "winning_weeks": 2,
              "evidence": [{"week": "2026-W41", "fit_edge": 1.05},
                           {"week": "2026-W42", "fit_edge": 0.95},
                           {"week": "2026-W43", "fit_edge": 1.05}]}])
        _t2 = _pg2["thresholds"][0.50]
        check("shared arc 2: the stamp grade's delayed-denial lens "
              "prices -$82.00 (window to the arm week) and the "
              "governance walk's -$180.40 canon adds the arm-lag "
              "week — the two-lens difference is exactly one week of "
              "the fit out-earning ($98.40)",
              _t2["delayed"] == 1
              and abs(_t2["net"] - (-82.00)) < 0.01
              and abs(abs(-180.40 - (-82.00)) - 98.40) < 0.01,
              (_t2.get("delayed"), _t2.get("net")))

check("the shared-arc canon agrees with the diag's own printed "
      "family ledger (one semantics, two consumers)",
      _pga.FAMILY_CANON["arc2"][0.50] == -180.40
      and _pga.FAMILY_CANON["arc3"][0.25] == 307.50
      and _pga.FAMILY_CANON["arc1"] == {0.25: None, 0.50: None},
      _pga.FAMILY_CANON)

# ── THE DISAGREEMENT ARCS (Sep 13): the shapes the live ledger
#    hasn't produced yet — a re-arm's POOL and its STREAK are
#    independent evidence, and each family's raise prices the shape
#    the OTHER dial sees. Both families walk every shared arc; the
#    canon values were machine-verified then hand-checked. ──────
for _arc in _pga.ARCS:
    _st, _stream, _canon = _arc["state"], _arc["stream"], _arc["canon"]
    _ffs = tbw._fbwgate_family_sweep(
        _stream, [{"state": _st, "action": a, "week": w}
                  for a, w in _canon])
    check(f"shared arc {_arc['key']}: the LIVE fbw walk's baseline "
          f"reproduces the canon (one seed rule, one pricing lens)",
          _ffs["baseline_ok"] is True, _ffs["lines"][:3])
    for _fw, _canon_net in _pga.FBW_CANON[_arc["key"]].items():
        _net = _ffs["family"].get(_fw)
        check(f"shared arc {_arc['key']}: the LIVE fbw walk at "
              f"FBW={_fw} prices the canon (${_canon_net:+,.2f})",
              _net is not None and abs(_net - _canon_net) < 0.01,
              (_net, _canon_net))
    _fs = tbw._poolgate_family_sweep(
        _stream, [{"state": _st, "action": a, "week": w}
                  for a, w in _canon])
    _pool_sum = round(sum(v for v in _fs["family"].values() if v), 2)
    _fbw_sum = round(sum(v for v in _ffs["family"].values() if v), 2)
    if _arc["key"] == "arc4":
        check("shared arc 4 (FAT POOL / THIN WEEKS): the disagreement "
              "priced — the fat-pool re-arm clears BOTH pool dials "
              "(family 0.0) while BOTH fbw raises DENY its thin "
              "2-week streak and MISS (the recovery was real)",
              _pool_sum == 0.0 and _fbw_sum == -53.30
              and _ffs["family"] == {3: -16.40, 4: -36.90},
              (_fs["family"], _ffs["family"]))
    if _arc["key"] == "arc5":
        check("shared arc 5 (THIN POOL / DURABLE RECOVERY): the "
              "mirror disagreement — BOTH pool raises deny the thin "
              "+0.175 re-arm and MISS $61.50 while its durable 4-week "
              "streak clears every fbw candidate (family 0.0)",
              _pool_sum == -123.00 and _fbw_sum == 0.0
              and _fs["family"] == {0.25: -61.50, 0.50: -61.50},
              (_fs["family"], _ffs["family"]))

def _fsmk(tag, di, do, src):
    return {"week": tag, "generated_ts": tag,
            "states": {"FLAT": {
                "in_force": {"fires": 10, "payers": 3, "rate": 0.3,
                             "usd": 10 * di, "usd_per_fire": di},
                "other": {"fires": 10, "payers": 3, "rate": 0.3,
                          "usd": 10 * do, "usd_per_fire": do},
                "other_src": src}}}


# the bench's origin pre-dates the ledger (flip@W39 seeds the walk;
# only in-ledger events must be REPRODUCED by the baseline)
_fs_flip = {"state": "FLAT", "action": "flip", "week": "2026-W39",
            "roll_delta": -0.5, "evidence": []}
_fs_ev = {"state": "FLAT", "action": "flip_back", "week": "2026-W41",
          "roll_delta": 0.3, "winning_weeks": 2,
          "evidence": [{"week": "2026-W40", "fit_edge": 0.2},
                       {"week": "2026-W41", "fit_edge": 0.4}]}
_fs_cost = [_fsmk("2026-W40", 2.0, 2.2, "outcome-fitted"),
            _fsmk("2026-W41", 2.4, 2.0, "quantile"),
            _fsmk("2026-W42", 1.0, 3.0, "quantile"),
            _fsmk("2026-W43", 1.0, 3.0, "quantile")]
_fs_pay = [_fsmk("2026-W40", 2.0, 2.2, "outcome-fitted"),
           _fsmk("2026-W41", 2.4, 2.0, "quantile"),
           _fsmk("2026-W42", 2.4, 2.0, "quantile"),
           _fsmk("2026-W43", 5.0, 2.0, "quantile")]
# the cascade arc, seat-coherent: the fit bleeds W42-W43 live, wins
# W44, goes fat W45 — the +0.50 denial holds the bench through all
# three, then the walked stream re-arms at W45 (the recovery's own
# pooled delta clears the bar) — so the fat week never diverges and
# the cascade prices ONCE: +20 +20 (W42-W43 saved) − 20 (W44 missed)
_fs_casc = _fs_cost + [_fsmk("2026-W44", 3.0, 1.0, "quantile"),
                       _fsmk("2026-W45", 12.0, 2.0, "quantile")]

_fs1 = tbw._poolgate_family_sweep(_fs_cost, [_fs_flip, _fs_ev])
check("family sweep: the walk denies the costing re-arm at +0.50 "
      "and the governance divergence prices the save ($40.00: the "
      "walked world keeps the quantile through the fit's bad "
      "W42-W43); +0.25 passes untouched; the baseline reproduces "
      "the lived events",
      _fs1["baseline_ok"] is True
      and _fs1["family"] == {0.25: 0.0, 0.5: 40.0}
      and any("+0.50: 2 divergent governance week(s)" in l
              and "net $+40.00 (SAVED)" in l for l in _fs1["lines"])
      and not _fs1["cascades"],
      (_fs1["family"], _fs1["baseline_ok"], _fs1["lines"][1:4]))
_fs2 = tbw._poolgate_family_sweep(_fs_pay, [_fs_flip, _fs_ev])
check("family sweep: denying a PAYING re-arm misses (net −$34.00) "
      "AND the walked stream re-arms later (W43) — the cascade "
      "EMERGES from the replay and the governance divergence prices "
      "it once (the fit's good W42-W43 benched: −$4 and −$30)",
      _fs2["family"] == {0.25: 0.0, 0.5: -34.0}
      and any("2 divergent governance week(s)" in l
              and "net $-34.00 (MISSED)" in l for l in _fs2["lines"])
      and _fs2["cascades"] == {0.5: [("2026-W43", "FLAT")]},
      (_fs2["family"], _fs2["cascades"], _fs2["lines"][1:5]))
_fs3 = tbw._poolgate_family_sweep(_fs_casc, [_fs_flip, _fs_ev])
check("family sweep: the cascade's path change prices ONCE — two "
      "benched-fit weeks save (+$40.00: W42-W43 the fit under-earned) "
      "against the walked world missing the recovery week AND the fat "
      "one (−$20 −$100: a W45 re-arm ships the fit only from W46 — "
      "the arm lag stamps never price) — one net, no week counted twice",
      _fs3["family"] == {0.25: 0.0, 0.5: -80.0}
      and any("4 divergent governance week(s)" in l
              and "net $-80.00 (MISSED)" in l for l in _fs3["lines"])
      and _fs3["cascades"] == {0.5: [("2026-W45", "FLAT")]},
      (_fs3["family"], _fs3["cascades"], _fs3["lines"][1:5]))
_fs4 = tbw._poolgate_family_sweep(_fs_cost, [_fs_flip])
check("family sweep: a trail that never recorded the bench's flip "
      "is a BASELINE DIVERGENCE, loudly — the walk refuses to price "
      "a ledger it cannot reproduce (no silent numbers)",
      _fs4["baseline_ok"] is False and _fs4["family"] == {}
      and any("BASELINE DIVERGENCE" in l for l in _fs4["lines"]),
      (_fs4["baseline_ok"], _fs4["lines"][1:3]))

# the embedding: grade_ledger carries the family section in the txt
# and the summary sidecar, re-priced from the REAL trail each run
# (the driver's ledger-signature wake re-runs it as flip-backs land)
import json as _json_fs
import tempfile as _tempfile_fs
_fs_sbx = Path(_tempfile_fs.mkdtemp())
_fs_saved = {k: getattr(tbw, k) for k in
             ("HISTORY_FILE", "FLIP_EVENTS_FILE", "FLIPGRADE_FILE",
              "FLIPGRADE_JSON", "FLIP_FILE", "RECKONING_WINDOW_FILE")}
try:
    tbw.HISTORY_FILE = _fs_sbx / "h.jsonl"
    tbw.FLIP_EVENTS_FILE = _fs_sbx / "evs.jsonl"
    tbw.FLIPGRADE_FILE = _fs_sbx / "fg.txt"
    tbw.FLIPGRADE_JSON = _fs_sbx / "fg.json"
    tbw.FLIP_FILE = _fs_sbx / "flips.json"
    tbw.RECKONING_WINDOW_FILE = _fs_sbx / "rkwin.jsonl"
    for r in _fs_cost:
        tbw.HISTORY_FILE.open("a", encoding="utf-8").write(
            _json_fs.dumps(r) + "\n")
    for e in (_fs_flip, _fs_ev,
              {"sim": True, "sim_source": "seeded replay",
               "state": "FLAT", "action": "flip_back",
               "week": "2026-W50", "roll_delta": -1.0,
               "evidence": []}):
        tbw.FLIP_EVENTS_FILE.open("a", encoding="utf-8").write(
            _json_fs.dumps(dict(e, ts="t")) + "\n")
    _fs_sum = tbw.grade_ledger()
    _fs_txt = tbw.FLIPGRADE_FILE.read_text(encoding="utf-8")
    _fs_sec = _fs_txt[_fs_txt.index("THE POOL-GATE FAMILY SWEEP"):] \
        if "THE POOL-GATE FAMILY SWEEP" in _fs_txt else ""
    check("family sweep: grade_ledger embeds the walk's section and "
          "sidecar (baseline_ok, family net, cascades) — sims never "
          "enter the priced ledger",
          _fs_sum.get("poolgate_family", {}).get("baseline_ok") is True
          and _fs_sum.get("poolgate_family", {}).get("net") \
          == {0.25: 0.0, 0.5: 40.0}
          and "2 divergent governance week(s)" in _fs_sec
          and "2026-W50" not in _fs_sec,
          (_fs_sum.get("poolgate_family"), bool(_fs_sec)))
finally:
    for k, v in _fs_saved.items():
        setattr(tbw, k, v)
    import shutil as _shutil_fs
    _shutil_fs.rmtree(_fs_sbx, ignore_errors=True)

fails = [r for r in results if not r[1]]
(scratch_space.report_temp('_tidebarweekly_check.txt')).write_text(
    "\n".join(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{d}]" if d and not ok else "")
              for name, ok, d in results)
    + f"\n\nRESULT: {len(results) - len(fails)} passed, {len(fails)} failed\n",
    encoding="utf-8")
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
