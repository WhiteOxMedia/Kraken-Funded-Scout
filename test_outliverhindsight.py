"""test_outliverhindsight.py — the rotation-outliver's hindsight pins.

The machinery: the exit ladder journals each outliver fire's
decision-time evidence once per rotation boundary
(outliver_fires.jsonl), and diag_outliverhindsight prices every fire
against the boundary the gate judged — saved or cost, in dollars.
These pins run everything HERMETICALLY (synthetic feather stores in
tmpdirs, module paths rebound, the scanner's clock frozen):

  1. THE JOURNAL — decision-time evidence stamped (bucket row, n,
     rec4, bmed, remaining minutes, age), the 60s repeat calls deduped
     per boundary, a thin-book fire carrying null row fields, and a
     journal failure never breaking the caller.
  2. THE STUDY — the four worlds: SAVED (the exit beat the boundary),
     COST (held into the rebound), PENDING (boundary not yet closed),
     and the re-touch thesis check (the book predicted no recovery;
     the study calls the miss out loud).
  3. THE EMPTY WORLD — a gate that has not fired prices zeros without
     exception (today's truth: zero fires since Sep 19).
  4. THE DRIVER — monthly_driver_loop ticks on a fast clock, appends
     per tick, stops cleanly (the family idiom).
  5. THE FAMILY — the scanner carries the journal call at the fire
     site and boots the driver with the family's conventions.
"""
import json
import sys
import threading
import time
from pathlib import Path

import pandas as pd

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

import diag_outliverhindsight as dol   # noqa: E402
import background_scanner as bs        # noqa: E402

PASS = FAIL = 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        lines.append(f"PASS  {name}")
    else:
        FAIL += 1
        lines.append(f"FAIL  {name}  [{detail}]")


def mk_store(path: Path, highs, closes, end=None):
    """The synthetic 1h store. Default start is the ANCHORED Sep-19
    grid the graded worlds' FIRE is pinned to; the PENDING world passes
    end= so its last bar is the current hour — the store must be as
    clock-relative as the fire it prices, or coverage ages out (the
    anchored store walked past its own last bar on Sep 26 and left the
    pending check red: a one-day time bomb, the same class as the old
    hard-coded 08:30 stamp this suite already caught once)."""
    n = len(closes)
    ts = (pd.date_range(end=end, periods=n, freq="h", tz="UTC")
          if end is not None
          else pd.date_range("2026-09-19", periods=n, freq="h", tz="UTC"))
    df = pd.DataFrame({"date": ts, "open": closes, "high": highs,
                       "low": [c * 0.99 for c in closes],
                       "close": closes, "volume": [1000.0] * n})
    df.to_feather(path)


FIRE = {"ts": "2026-09-19T04:00:00+00:00", "pair": "ZEC/USD",
        "live": 35.0, "size": 2500.0, "usd": -357.0, "dip_pct": 12.5,
        "bucket": "4.5-100.0", "n": 31, "rec4": 0.13, "bmed_h": 11.0,
        "remaining_min": 180, "high": 40.0}

# ── sandbox paths for the whole suite ──
import tempfile   # noqa: E402
import scratch_space   # scratch policy: nothing volatile lands in the repo root
TMP = Path(tempfile.mkdtemp())
_dol_fires = TMP / "fires.jsonl"
_dol_out = TMP / "out.txt"
_dol_ledger = TMP / "led.jsonl"
_dol_stores = TMP / "stores"
_dol_stores.mkdir()
_bs_fires = TMP / "scanner_fires.jsonl"
_real = (dol.FIRES_FILE, dol.OUT, dol.LEDGER, dol.STORES,
         bs._OUTLIVER_FIRES_FILE)
_real_prof = bs.coin_wiggle_profile   # captured BEFORE the try: the finally's restore can never NameError

try:
    # the sandbox swaps live INSIDE the try (the PULSE_JSON pattern):
    # a crash between capture and the finally below must never strand
    # the production paths at the tempdir for the rest of the process
    dol.FIRES_FILE, dol.OUT, dol.LEDGER, dol.STORES = (
        _dol_fires, _dol_out, _dol_ledger, _dol_stores)
    bs._OUTLIVER_FIRES_FILE = _bs_fires

    # 1. THE JOURNAL
    _real_now = bs._now_mst
    bs._now_mst = lambda: _real_now().replace(hour=13, minute=42)
    bs.coin_wiggle_profile = lambda p: {
        "env": 9.0, "n": 100, "buckets": {
            str((4.5, 100.0)): {"n": 31, "rec": 0.55, "rec4": 0.13,
                                "bmed": 11.0}}}
    bs._OUTLIVER_JOURNAL_BOUNDARY.clear()
    bs._journal_outliver_fire("ZEC/USD", 40.0, 35.0, 40.0, 2500.0,
                              -312.5, 12.5, "usually misses the rotation",
                              age_min=95.2)
    bs._journal_outliver_fire("ZEC/USD", 40.0, 34.8, 40.0, 2500.0,
                              -325.0, 13.0, "repeat 60s later",
                              age_min=96.0)
    rows = [json.loads(l) for l in _bs_fires.read_text(
        encoding="utf-8").splitlines() if l.strip()]
    check("journal: the fire stamps decision-time evidence (bucket row, "
          "n, rec4, bmed, remaining minutes, age, P&L at fire)",
          len(rows) == 1 and rows[0]["bucket"] == "4.5-100.0"
          and rows[0]["n"] == 31 and rows[0]["rec4"] == 0.13
          and rows[0]["bmed_h"] == 11.0 and rows[0]["age_min"] == 95.2
          and rows[0]["usd"] == -312.5 and rows[0]["remaining_min"] > 0,
          json.dumps(rows[0])[:200])
    check("journal: the 60s repeat calls dedupe per boundary (one "
          "decision, not five hearings)",
          len(rows) == 1 and rows[0]["usd"] == -312.5)
    bs.coin_wiggle_profile = lambda p: None
    bs._journal_outliver_fire("NOBOOK/USD", 1.0, 0.85, 1.0, 2500.0,
                              -375.0, 15.0, "thin book", age_min=60.0)
    rows2 = [json.loads(l) for l in _bs_fires.read_text(
        encoding="utf-8").splitlines() if l.strip()]
    check("journal: a thin-book fire journals its DEPTH (the bucket "
          "still names itself) with null coin evidence (n/rec4/bmed) — "
          "the grade prices the move regardless",
          len(rows2) == 2 and rows2[1]["bucket"] == "4.5-100.0"
          and rows2[1]["n"] is None and rows2[1]["rec4"] is None
          and rows2[1]["bmed_h"] is None)
    # a journal failure never breaks the caller
    bs._OUTLIVER_FIRES_FILE = TMP  # a DIRECTORY: the append raises
    bs._journal_outliver_fire("X/USD", 1.0, 0.9, 1.0, 100.0, -5.0, 10.0,
                              "boom", age_min=60.0)
    check("journal: a write failure is swallowed at debug and the "
          "caller survives", True)
    bs._OUTLIVER_FIRES_FILE = _bs_fires
    bs._now_mst = _real_now
    bs.coin_wiggle_profile = _real_prof

    # 2. THE STUDY — the four worlds on a synthetic store.
    highs = [39.0] * 200          # below the 40.0 reference: no re-touch
    closes = [40.0] * 200
    closes[4] = 35.0; closes[5] = 34.0; closes[6] = 33.5
    closes[7] = 33.0; closes[8] = 34.0; closes[9] = 36.0
    closes[10] = 40.0
    mk_store(_dol_stores / "ZEC_USD-1h.feather", highs, closes)
    _dol_fires.write_text(json.dumps(FIRE) + "\n", encoding="utf-8")
    s = dol.run_study()
    check("study: the SAVED world — the exit beat the boundary by the "
          "hand-checked arithmetic ((35-33) x 2500/35 = +142.86), the "
          "book's no-re-touch thesis held",
          s["graded"] == 1 and abs(s["net_usd"] - 142.86) < 0.5
          and s["rows"][0]["verdict"] == "SAVED" and s["n_retouch"] == 0,
          json.dumps(s)[:220])
    _dol_ledger.write_text("", encoding="utf-8")
    _dol_fires.write_text(json.dumps({**FIRE, "remaining_min": 360})
                          + "\n", encoding="utf-8")
    s2 = dol.run_study()
    check("study: the COST world — holding to the boundary rode the "
          "rebound back to 40.0 (net -357.14), the exit cost",
          s2["graded"] == 1 and abs(s2["net_usd"] + 357.14) < 0.5
          and s2["rows"][0]["verdict"] == "COST",
          json.dumps(s2)[:200])
    _dol_ledger.write_text("", encoding="utf-8")
    # clock-relative pending: one minute ago + a 240-min boundary = it can
    # never close while the suite runs (the old hard-coded 08:30 stamp
    # aged past its own boundary on Sep 20 — a time bomb, not a pin)
    from datetime import datetime as _dtu, timedelta as _tdu, timezone as _tzu
    _pending_ts = (_dtu.now(_tzu.utc) - _tdu(minutes=1)).isoformat(timespec="seconds")
    _dol_fires.write_text(json.dumps(
        {**FIRE, "ts": _pending_ts,
         "remaining_min": 240}) + "\n", encoding="utf-8")
    # the store rides the same clock: its LAST bar is the fire's hour,
    # so the fresh fire always lands inside coverage (grade_fire's
    # 1h-bar tolerance) while its 240-min boundary stays in the future
    mk_store(_dol_stores / "ZEC_USD-1h.feather", highs, closes,
             end=_dtu.fromisoformat(_pending_ts).replace(
                 minute=0, second=0, microsecond=0))
    s3 = dol.run_study()
    check("study: the PENDING world — a fire whose judged boundary has "
          "not closed waits honestly (never priced before it exists; "
          "fire AND store are clock-relative — the anchored store aged "
          "out of coverage on Sep 26 and left this red)",
          s3["pending"] == 1 and s3["graded"] == 0, json.dumps(s3)[:160])
    _dol_ledger.write_text("", encoding="utf-8")
    _dol_fires.write_text(json.dumps(FIRE) + "\n", encoding="utf-8")
    _hs = [40.5 if i == 5 else 39.0 for i in range(200)]
    mk_store(_dol_stores / "ZEC_USD-1h.feather", _hs, closes)
    s4 = dol.run_study()
    check("study: the re-touch thesis — a coin that climbed back to its "
          "pre-dip high within 24 bars is CALLED OUT (the book was "
          "wrong for this dip)",
          s4["n_retouch"] == 1 and s4["rows"][0]["retouched"] is True,
          json.dumps(s4["rows"])[:200])
    _dol_ledger.write_text("", encoding="utf-8")

    # 3. THE EMPTY WORLD — zero fires, zeros, no exception.
    _dol_fires.write_text("", encoding="utf-8")
    s0 = dol.run_study()
    check("empty world: a gate that has not fired prices zeros without "
          "exception (today's truth — the journal accrues when it "
          "speaks)",
          s0["fires_total"] == 0 and s0["graded"] == 0
          and s0["net_usd"] == 0.0, json.dumps(s0)[:160])

    # 4. THE DRIVER — fast-clock ticks, clean stop.
    _dol_fires.write_text(json.dumps(FIRE) + "\n", encoding="utf-8")
    stop = threading.Event()
    th = threading.Thread(target=dol.monthly_driver_loop,
                          args=(stop, 0.05, 0.2), daemon=True)
    th.start()
    time.sleep(1.2)
    stop.set()
    th.join(timeout=5)
    led = [json.loads(l) for l in _dol_ledger.read_text(
        encoding="utf-8").splitlines() if l.strip()]
    check("driver: appended per tick and stopped cleanly",
          not th.is_alive() and len(led) >= 2, f"{len(led)} records")

    # 5. THE FAMILY — the scanner journals at the fire site and boots
    # the driver.
    bs_src = (BASE / "background_scanner.py").read_text(encoding="utf-8")
    check("family: the scanner journals the fire at the outliver branch "
          "and boots the hindsight driver with the family's conventions",
          "_journal_outliver_fire(pair, entry, live, high, size" in bs_src
          and 'name="outliverhindsight-monthly"' in bs_src
          and "appends _outliverhindsight_monthly.jsonl" in bs_src
          and "_doh.FIRST_DELAY_S" in bs_src
          and dol.FIRST_DELAY_S == 900.0
          and dol.MONTHLY_PERIOD_S == 30 * 86400.0,
          "fire-site call or driver block missing")
finally:
    (dol.FIRES_FILE, dol.OUT, dol.LEDGER, dol.STORES) = _real[:4]
    bs._OUTLIVER_FIRES_FILE = _real[4]
    bs.coin_wiggle_profile = _real_prof   # the wiggle swap's restore-in-finally (leak gate)

lines.append(f"RESULT: {PASS} passed, {FAIL} failed")
(scratch_space.report_temp('_outliverpins_check.txt')).write_text("\n".join(lines) + "\n",
                                              encoding="utf-8")
print("\n".join(lines))
sys.exit(1 if FAIL else 0)
