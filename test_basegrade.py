"""test_basegrade.py — the fixed-hour grade's driver-family pins.

The study (diag_basegrade.py) grades the retired "about an hour" base
call against each coin's own median base minutes from the deep stores.
Since Sep 20 it is a member of the monthly evidence-driver family: the
scanner boots a daemon that re-prices the fleet monthly and appends
_basegrade_monthly.jsonl. These pins run the machinery HERMETICALLY —
synthetic feather stores in tmpdirs, module paths rebound — so the
battery never touches the production stores or ledger:

  1. THE WALK — an engineered dip recovering after N bars prices
     N*60 minutes; a dip that never recovers inside the rotation is
     not a sample; a shallow dip (< 0.5%) is not a sample; an
     unreadable store is skipped, never fatal.
  2. THE STUDY — run_study over a sandbox dir grades/skips honestly,
     writes the txt, appends the ledger, and returns the summary the
     record carries (fleet median, late/fast counts, buckets).
  3. THE EMPTY WORLD — an empty stores dir prices zeros without
     exception (the month a store retires stays comparable).
  4. THE DRIVER — monthly_driver_loop ticks on a fast clock, appends
     per tick, and stops cleanly (the clampcost family idiom).
  5. THE FAMILY — the scanner's driver-family block carries the
     basegrade daemon with the family's name and clock, so the
     membership cannot silently regress.
"""
import json
import sys
import threading
import time
from pathlib import Path

import pandas as pd

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

import diag_basegrade as dbg   # noqa: E402

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


def mk_store(path: Path, closes, highs):
    n = len(closes)
    ts = pd.date_range("2026-08-01", periods=n, freq="h")
    df = pd.DataFrame({
        "date": ts, "open": closes, "high": highs,
        "low": [c * 0.99 for c in closes], "close": closes,
        "volume": [1000.0] * n})
    df.to_feather(path)


def flat_book(n=500, base=100.0, hh=102.0):
    """A coin whose highs sit at hh and closes at base — every bar is a
    2%-dip bar that recovers NEXT bar (2 bars)."""
    closes = [base] * n
    highs = [hh if i >= 24 else 0.5 for i in range(n)]
    return closes, highs


# ── sandbox paths for the whole suite ──
import tempfile   # noqa: E402
TMP = Path(tempfile.mkdtemp())
_dbg_ledger = TMP / "ledger.jsonl"
_dbg_out = TMP / "out.txt"
_dbg_stores = TMP / "stores"
_dbg_stores.mkdir()
_real_ledger, _real_out, _real_stores = dbg.LEDGER, dbg.OUT, dbg.STORES
dbg.LEDGER, dbg.OUT, dbg.STORES = _dbg_ledger, _dbg_out, _dbg_stores

try:
    # 1. THE WALK — engineered base times on a synthetic store.
    closes, highs = flat_book()
    # dip bars 60-61 recover at bar 63 (1 bar of dip + next high) ->
    # bars-to-base = 2 -> 120 min. Same at 200-201.
    for i in (60, 61, 200, 201):
        closes[i] = 98.5          # (102-98.5)/102 = 3.43% dip
        highs[i] = 98.0           # the dip bar's own high is below hh
    f = _dbg_stores / "AAA_USD-1h.feather"
    mk_store(f, closes, highs)
    got = dbg.walk_store(f)
    check("walk: a readable store returns (raw, bucket map, bucket "
          "totals, bucket rec4 counts)",
          got is not None and len(got) == 4, repr(got)[:120])
    raw, bmap, btot, brec4 = got
    # the walk's window: bars n-500..n-25 minus the bar-24 warmup — the
    # engineered dips sit inside it; every flat bar is a 2% dip that
    # recovers in 1 bar, the two engineered dips recover in 2 bars.
    check("walk: flat-bar dips recover in 1 bar (60 min), engineered "
          "3-bar dips in 2 bars (120 min)",
          60.0 in raw and 120.0 in raw,
          f"raw[:6]={sorted(set(raw))[:6]}")
    b = dbg.bucket_of(3.43)
    check("walk: the engineered dips land in the book's 3.0-4.5 bucket",
          b == (3.0, 4.5) and b in bmap and 120.0 in bmap[b],
          f"{b} in {sorted(bmap.keys())}")
    check("walk: rec4 arithmetic — every flat dip (1.96% deep, the "
          "1.5-2.0 bucket) recovered in 1-3 bars (<= 4), so that "
          "bucket's rec-rate is 1.0",
          btot.get((1.5, 2.0), 0) > 0
          and brec4.get((1.5, 2.0), 0) == btot.get((1.5, 2.0)),
          f"btot={btot.get((1.5, 2.0))} brec4={brec4.get((1.5, 2.0))}")
    check("walk: the engineered 3-bar dips count in the (3.0,4.5) "
          "rate's numerator too",
          btot.get((3.0, 4.5), 0) == 4
          and brec4.get((3.0, 4.5), 0) == 4,
          f"btot={btot.get((3.0, 4.5))} brec4={brec4.get((3.0, 4.5))}")

    # a dip that never recovers inside the rotation is NOT a sample:
    closes2, highs2 = flat_book()
    for i in range(300, 480):
        closes2[i] = 90.0         # a deep, never-recovering trough
        highs2[i] = 91.0
    f2 = _dbg_stores / "BBB_USD-1h.feather"
    mk_store(f2, closes2, highs2)
    raw2, _, btot2, brec42 = dbg.walk_store(f2)
    check("walk: a never-recovering dip contributes no sample "
          "(the GET OUT case is not a base-time sample)",
          all(m <= 120.0 for m in raw2), f"max={max(raw2)}")
    check("walk: a never-recovering dip STAYS in the rec-rate's "
          "denominator — the deep bucket counts it and scores rec4 0.0 "
          "(the fleet fallback's death evidence)",
          btot2.get((4.5, 100.0), 0) >= 24
          and brec42.get((4.5, 100.0), 0) == 0,
          f"btot={btot2.get((4.5, 100.0))} brec4={brec42.get((4.5, 100.0))}")

    # a shallow dip (< 0.5%) is not a sample: closes sit 0.05% under
    # the highs everywhere — every bar is a dip, none clears the gate
    closes3 = [99.95] * 500
    highs3 = [100.0 if i >= 24 else 0.5 for i in range(500)]
    f3 = _dbg_stores / "CCC_USD-1h.feather"
    mk_store(f3, closes3, highs3)
    got3 = dbg.walk_store(f3)
    check("walk: a store with no qualifying dips returns None — it "
          "has no base-time book (the study skips it)",
          got3 is None, repr(got3)[:80])

    # an unreadable store is skipped, never fatal:
    junk = _dbg_stores / "DDD_USD-1h.feather"
    junk.write_text("not a feather", encoding="utf-8")
    check("walk: an unreadable store returns None",
          dbg.walk_store(junk) is None)

    # 2. THE STUDY — sandbox run grades/skips honestly and writes both.
    s = dbg.run_study(stores_dir=_dbg_stores, anchor=False)
    check("study: the sandbox run grades the readable stores and "
          "skips the bookless + junk ones",
          s["graded"] == 2 and s["skipped"] == 2 and s["coins_total"] == 4,
          f"{s['graded']}/{s['coins_total']} skipped {s['skipped']}")
    check("study: the summary carries the fleet verdict the ledger "
          "prices — claim, median, late/fast, buckets",
          s["claim_min"] == 60.0 and s["samples"] > 0
          and s["fleet_median_min"] > 0
          and isinstance(s["buckets"], dict) and s["buckets"]
          and s["top_offenders"] and s["top_offenders"][0]["pair"]
          .endswith("_USD"),
          json.dumps(s)[:200])
    check("study: the txt and the ledger both landed on the sandbox "
          "paths",
          _dbg_out.exists() and _dbg_ledger.exists()
          and "FLEET VERDICT" in _dbg_out.read_text(encoding="utf-8"),
          f"txt={_dbg_out.exists()} ledger={_dbg_ledger.exists()}")
    led = [json.loads(l) for l in _dbg_ledger.read_text(encoding="utf-8")
           .splitlines() if l.strip()]
    check("ledger: the record round-trips the study's verdict",
          len(led) == 1 and led[0]["graded"] == s["graded"]
          and led[0]["fleet_median_min"] == s["fleet_median_min"]
          and "ts" in led[0], sorted(led[0].keys()))
    check("ledger: every bucket carries the rec4 rate leg the "
          "outliver's fleet fallback reads",
          led[0]["buckets"]
          and all(v.get("rec4") is not None
                  for v in led[0]["buckets"].values()),
          json.dumps(led[0]["buckets"])[:200])

    # 3. THE EMPTY WORLD — zeros, never an exception.
    empty = TMP / "empty"
    empty.mkdir()
    s2 = dbg.run_study(stores_dir=empty, anchor=False)
    check("empty world: zeros without exception",
          s2["graded"] == 0 and s2["samples"] == 0
          and s2["fleet_median_min"] == 0.0 and s2["buckets"] == {},
          json.dumps(s2)[:160])

    # 4. THE DRIVER — fast-clock ticks, clean stop.
    _dbg_ledger.write_text("", encoding="utf-8")
    dbg.STORES = empty            # the driver's run_study() walks this
    stop = threading.Event()
    th = threading.Thread(target=dbg.monthly_driver_loop,
                          args=(stop, 0.05, 0.2), daemon=True)
    th.start()
    time.sleep(1.2)
    stop.set()
    th.join(timeout=5)
    led2 = [json.loads(l) for l in _dbg_ledger.read_text(encoding="utf-8")
            .splitlines() if l.strip()]
    check("driver: appended per tick and stopped cleanly",
          not th.is_alive() and len(led2) >= 2,
          f"{len(led2)} records")

    # 5. THE FAMILY — the scanner boots the daemon.
    bs_src = (BASE / "background_scanner.py").read_text(encoding="utf-8")
    check("family: the scanner's driver-family block carries the "
          "basegrade daemon with the family's conventions",
          "import diag_basegrade as _dbg" in bs_src
          and 'name="basegrade-monthly"' in bs_src
          and "appends _basegrade_monthly.jsonl" in bs_src
          and "_dbg.FIRST_DELAY_S" in bs_src
          and dbg.FIRST_DELAY_S == 900.0
          and dbg.MONTHLY_PERIOD_S == 30 * 86400.0,
          "driver block missing or clock drift")
finally:
    dbg.LEDGER, dbg.OUT, dbg.STORES = _real_ledger, _real_out, _real_stores

lines.append(f"RESULT: {PASS} passed, {FAIL} failed")
(BASE / "_basegradepins_check.txt").write_text("\n".join(lines) + "\n",
                                               encoding="utf-8")
print("\n".join(lines))
sys.exit(1 if FAIL else 0)
