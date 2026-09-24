"""test_costdrivers.py — pins for the two recurring-cost drivers added
Sep 16 (the clamp-cost driver was the template; these two price the
other recurring decisions):

  1. TIDE-HINDSIGHT (diag_tidememory_hindsight): the per-coin tide
     memory's dollar hindsight re-prices itself monthly off the growing
     outcomes ledger (_tidehindsight_monthly.jsonl).
  2. NO-CHASE FLEET (diag_nochase_fleet): the no-chase ladder replays
     every 1h store through the one-gate counterfactual monthly
     (_nochasefleet_monthly.jsonl); the RSI line is the decider only
     where a decisive refusal exists.

Both run HERMETICALLY here: ledger/txt/outcome paths redirected to a
tmpdir, the fleet pass bounded to 2 stores (deterministic slice), the
fleet driver's tick test stubbed (the real 42-min pass belongs to the
driver thread, never to the battery).
"""
import json
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import tempfile

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(detail) if not cond and detail else ""))


tmp = Path(tempfile.mkdtemp(prefix="costdrivers_"))

# ── 1. the tide-hindsight driver ─────────────────────────────────────
import diag_tidememory_hindsight as dth
import scratch_space   # scratch policy: nothing volatile lands in the repo root

_saved = {k: getattr(dth, k) for k in ("OUT", "OUTCOMES", "LEDGER")}
try:
    dth.OUT = tmp / "th.txt"
    dth.OUTCOMES = tmp / "outcomes.jsonl"
    dth.LEDGER = tmp / "th_monthly.jsonl"
    # two synthetic graded burials: a priced tide-out with the bar text
    # the binder prices, and a wall row (the bot bridge's OUT wall)
    rows = [
        {"ts": "2026-09-16T09:00:00-06:00", "pair": "AAA/USD",
         "gate": "tide-out", "tide": "OUT 30%", "quick": 64.0,
         "brain": 70.0, "floor": None,
         "detail": "quick 64.0 vs personal bar 65 (learned OUT bar 65)",
         "outcome": {"end_move_pct": 1.2, "mfe_pct": 1.9,
                     "payoff": "grinder"}},
        {"ts": "2026-09-16T09:30:00-06:00", "pair": "BBB/USD",
         "gate": "tide-out", "tide": "OUT 25%", "quick": 70.0,
         "brain": None, "floor": None,
         "detail": "all ships sink together; no solo move",
         "outcome": {"end_move_pct": -0.8, "mfe_pct": 0.4,
                     "payoff": "dud"}},
    ]
    dth.OUTCOMES.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    s = dth.run_study()
    check("hindsight: the study prices the binder-priced row and names the wall",
          s["priced"] == 1 and s["wall_burials"] == 1, s)
    check("hindsight: the record carries the study's dollars + live position",
          "released_end_usd" in s and s["position_usd"] > 0, s)
    led = [json.loads(l) for l in dth.LEDGER.read_text(
        encoding="utf-8").splitlines() if l.strip()]
    check("hindsight: run_study appends one ledger record",
          len(led) == 1 and led[0]["priced"] == 1, led)
    # the driver: fast-clock ticks, clean stop
    stop = threading.Event()
    th = threading.Thread(target=dth.monthly_driver_loop,
                          args=(stop, 0.05, 0.4), daemon=True)
    th.start()
    time.sleep(1.4)
    stop.set()
    th.join(timeout=5)
    led2 = [json.loads(l) for l in dth.LEDGER.read_text(
        encoding="utf-8").splitlines() if l.strip()]
    check("hindsight: the driver appends per tick and stops cleanly",
          not th.is_alive() and len(led2) > len(led),
          f"{len(led2)} records (was {len(led)})")
finally:
    for k, v in _saved.items():
        setattr(dth, k, v)

_src = (BASE / "diag_tidememory_hindsight.py").read_text(encoding="utf-8")
check("hindsight: the driver is wired into the scanner's family",
      "tidehindsight-monthly" in (BASE / "background_scanner.py").read_text(
          encoding="utf-8"))
check("hindsight: dollars stay current (the live contest-size accessor)",
      "_position_usd" in _src and "_contest_size" in _src)
check("hindsight: the monthly cadence + CLI survive",
      "MONTHLY_PERIOD_S = 30 * 86400.0" in _src
      and 'if __name__ == "__main__":' in _src and "main()" in _src)
check("hindsight: the no-look-ahead contract is intact (truncated rebuild)",
      "_store_adj_at" in _src and "truncated" in _src.lower())

# ── 2. the ladder-grade driver (supersedes nochase-fleet) ────────────
import diag_laddergrade as dlg

_saved_f = {k: getattr(dlg, k) for k in ("OUT", "LEDGER")}
try:
    dlg.OUT = tmp / "lg.txt"
    dlg.LEDGER = tmp / "lg_monthly.jsonl"
    # the bounded pass: 2 contest stores through the REAL per-rung
    # counterfactuals
    s = dlg.run_study(max_pairs=2)
    check("ladder: the bounded pass grades real stores and reports the bound",
          s["pairs"] == 2 and s["bounded"] is True and s["hours_total"] > 0, s)
    check("ladder: every rung carries a verdict + dollars",
          set(s["rungs"]) == {"rsi", "pullback", "knife", "score"}
          and all({"net_usd", "verdict"} <= set(v) for v in s["rungs"].values()),
          s["rungs"])
    txt = dlg.OUT.read_text(encoding="utf-8")
    check("ladder: the txt marks the bounded sample (the driver runs the 54)",
          "BOUNDED SAMPLE" in txt)
    check("ladder: the txt carries the per-rung table with verdicts",
          "PER RUNG, FLEET-WIDE" in txt and "DEAD WEIGHT" in txt)
    led = [json.loads(l) for l in dlg.LEDGER.read_text(
        encoding="utf-8").splitlines() if l.strip()]
    check("ladder: run_study appends one ledger record",
          len(led) == 1 and led[0]["pairs"] == 2, led)

    # no contest stores at all: zeros, never an exception
    dlg_out = dlg.OUT
    _saved_glob = dlg.STORES_GLOB
    dlg.STORES_GLOB = "surething_data/kraken/*_NOPE-1h.feather"
    try:
        s2 = dlg.run_study()
        check("ladder: an empty store glob degrades to zeros without exception",
              s2["pairs"] == 0 and all(v["net_usd"] == 0.0
                                       for v in s2["rungs"].values()), s2)
    finally:
        dlg.STORES_GLOB = _saved_glob
finally:
    for k, v in _saved_f.items():
        setattr(dlg, k, v)

# the driver tick: run_study stubbed (the ~30-min fleet pass is not the
# battery's job — the loop mechanics are what the pin owns)
_calls = []


def _stub_run(max_pairs=None):
    _calls.append(max_pairs)
    dlg._append({"ts": "stub", "pairs": 0, "rungs": {}})
    return {"pairs": 0, "rungs": {"score": {"verdict": "x", "net_usd": 0.0}}}


_saved_rs = dlg.run_study
dlg.OUT = tmp / "lg2.txt"
dlg.LEDGER = tmp / "lg2_monthly.jsonl"
try:
    dlg.run_study = _stub_run
    stop = threading.Event()
    th = threading.Thread(target=dlg.monthly_driver_loop,
                          args=(stop, 0.05, 0.4), daemon=True)
    th.start()
    time.sleep(1.4)
    stop.set()
    th.join(timeout=5)
    led2 = [json.loads(l) for l in dlg.LEDGER.read_text(
        encoding="utf-8").splitlines() if l.strip()]
    check("ladder: the driver appends per tick and stops cleanly",
          not th.is_alive() and len(led2) >= 2, f"{len(led2)} records")
finally:
    dlg.run_study = _saved_rs

_srcl = (BASE / "diag_laddergrade.py").read_text(encoding="utf-8")
check("ladder: the driver is wired into the scanner's family",
      "laddergrade-monthly" in (BASE / "background_scanner.py").read_text(
          encoding="utf-8"))
check("ladder: each module rung's counterfactual is a one-line disable (uniqueness asserted)",
      'rsi_ceiling = 78.0 if trending_up else 68.0' in _srcl
      and 'src.count(r["needle"]) == 1' in _srcl
      and '"score floor' in _srcl)
check("ladder: the score rung rides a cfg override (min_confidence=0), no patching",
      "min_confidence=0.0" in _srcl)
check("ladder: SELL hours are nobody's court (only blocked BUYs are priced)",
      'action == "sell"' in _srcl)
check("ladder: the heavy pass lives in the driver (boot-seed + monthly)",
      "FIRST_DELAY_S = 2700.0" in _srcl
      and "MONTHLY_PERIOD_S = 30 * 86400.0" in _srcl)
check("ladder: the 54 = the config's contest list (not the whole feather dir)",
      "cfg.trading_pairs" in _srcl)

# ── RUN ──────────────────────────────────────────────────────────────
fails = [r for r in results if not r[1]]
(scratch_space.report_temp('_costdrivers_check.txt')).write_text(
    "\n".join(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{d}]" if d and not ok else "")
              for name, ok, d in results)
    + f"\n\nRESULT: {len(results) - len(fails)} passed, {len(fails)} failed\n",
    encoding="utf-8")
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
