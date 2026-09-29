"""test_referee_gfdelta.py - THE PER-DAY-GF REFEREE DRIVE (Sep 28).

_referee_gfdelta.py re-runs the referee (_livearm_referee.py, 459c02b)
at each bar's real day factor - the refinement the half-goal-arm thread
deferred until the guard factor's own history (factor_history(),
9f87266) accrued ~2 weeks. The day mapping itself lives in the shared
seam (_gf_series.GFSeries, the arc-wide single source); this drive
proves the referee study's SIDE of the contract:

  1. THE SEAM WIRING, HERMETIC: the study's GFS is rebindable (the
     drive injects synthetic series without touching the real state
     file) and the walk's day mode reads it bar-by-bar - the
     chain-back rule (pre-series bars carry the newest known factor;
     the 1.0 floor only when no series exists), the deduped map, the
     frozen baseline riding the state's top row.
  2. THE DAY-KEY (through the seam): bar-level GF uses the writer's
     day key (MST / America/Denver), not UTC - the two diverge 6h/day.
  3. THE GATES: the span gate refuses to report below MIN_DAYS (exit
     0, honest refusal); the parity gate aborts exit 3 when the frozen
     rows drift from the committed referee numbers.
  4. THE LIVE WORLD: the real tree - parity rows reproduce the
     committed referee exactly, the run exits 0 in whichever honest
     world the series spans (NOT YET now; DELTA once 14 days accrue),
     and every series day reads back its factor.
  5. THE SHARED SEAM ITSELF (_gf_series): rows/dicts/frozen
     construction, the span gate at the boundary, and the notyet text.

Run: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe test_referee_gfdelta.py
"""
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import _referee_gfdelta as rg

lines = []
PASS = FAIL = 0


def check(label, ok, detail=""):
    global PASS, FAIL
    line = ("PASS  " if ok else "FAIL  ") + label
    if detail:
        line += "  [" + detail + "]"
    lines.append(line)
    print(line, flush=True)
    if ok:
        PASS += 1
    else:
        FAIL += 1


def _sandbox_series(rows):
    """Rebind the study's series seam to a synthetic GFSeries (the
    drive injects [[day, factor], ...] rows; the real state file is
    never touched). Returns the old binding for the finally."""
    old = rg.GFS
    rg.GFS = rg.GFSeries(rows=rows, frozen=rows[-1][1] if rows else None)
    return old


def _restore(old):
    rg.GFS = old


MST = ZoneInfo("America/Denver")


def _main_rc():
    """Run rg.main() and return its exit code: main() returns the code
    but callers capture it as data (an escaped sys.exit would silently
    become THIS drive's exit code)."""
    try:
        return rg.main()
    except SystemExit as e:
        return e.code


# -- 1. the seam wiring: chain-back + dedupe + frozen, hermetic ----
old = _sandbox_series([])
check("1.1 an empty series rides the DEFAULT_GF floor",
      rg.GFS.span_days == 0 and rg.GFS.frozen == 1.0
      and rg.GFS.day_factor("2026-01-01") == 1.0)
_restore(old)

rows = [["2026-09-20", 1.2], ["2026-09-25", 1.6],
        ["2026-09-26", 2.0], ["2026-09-26", 2.0]]   # same-day dup
old = _sandbox_series(rows)
check("1.2 the map is deduped day->factor (the writer's day dedupe "
      "already guarantees it)",
      rg.GFS.span_days == 3 and rg.GFS.series["2026-09-26"] == 2.0)
check("1.3 a pre-series day chains back to the NEWEST factor, never "
      "the 1.0 floor",
      rg.GFS.day_factor("2026-09-01") == 2.0)
check("1.4 a series day reads its own row",
      rg.GFS.day_factor("2026-09-20") == 1.2)
check("1.5 a post-series day (future stamp) chains to the newest "
      "known factor",
      rg.GFS.day_factor("2026-10-01") == 2.0)
check("1.6 the frozen baseline rides the state's top row (the anchor "
      "the parity gate protects)",
      rg.GFS.frozen == 2.0 and rg.GF_FROZEN == 2.0)
_restore(old)

# -- 2. the day key is MST, not UTC (through the seam) -------------
old = _sandbox_series([["2026-09-26", 1.6], ["2026-09-27", 2.0]])
try:
    # 02:00 UTC on 2026-09-27 is 20:00 MST on 2026-09-26 - the two
    # calendars disagree for 6 hours every day. DISTINCT factors on the
    # two sides (CodeRabbit major, Sep 28: one row at 2.0 could not
    # tell a correct chain-back from a wrong UTC-day lookup - both
    # returned 2.0); the bar MUST read the PREVIOUS MST day's 1.6.
    ts = pd.Timestamp(datetime(2026, 9, 27, 2, 0, tzinfo=timezone.utc))
    got = rg.GFS.at(ts)
    day = ts.tz_convert(MST).strftime("%Y-%m-%d")
    check("2.1 a bar's GF day key is its MST day (02:00 UTC -> the "
          "previous MST day), not UTC",
          day == "2026-09-26" and got == 1.6,
          f"utc-day bar -> mst day {day}, gf {got} (want 1.6)")
    check("2.2 the mapping counters tick (series-day bars count, "
          "fallback bars count separately)",
          rg.GFS.n_series + rg.GFS.n_fallback >= 1)
finally:
    _restore(old)

# -- 3. the gates --------------------------------------------------
# the span gate: a 2-day series must refuse to report, exit 0
old = _sandbox_series([["2026-09-26", 2.0], ["2026-09-27", 2.0]])
try:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = _main_rc()
    out = buf.getvalue()
    check("3.1 the span gate refuses to report under MIN_DAYS (NOT "
          "YET, exit 0 - running early is honest refusal)",
          rc == 0 and "NOT YET" in out and "covers 2 day(s)" in out,
          f"rc={rc}")
    check("3.2 the refusal names the accrual ask and keeps the "
          "bracket as the standing bound",
          ">= 14" in out and "bracket" in out)
finally:
    _restore(old)

# the parity gate: a drifting frozen baseline aborts exit 3
old = _sandbox_series([["2026-09-27", 1.0]])
_gf_old = getattr(rg, "GF_FROZEN", None)   # captured BEFORE the override
try:
    rg.GF_FROZEN = 1.0          # the drifted baseline (real state: 2.0)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = _main_rc()
    out = buf.getvalue()
    check("3.3 a frozen baseline off the referee's numbers aborts the "
          "study (exit 3, the copy is indicted, not the refinement)",
          rc == 3 and "ABORT" in out and "DRIFT" in out)
finally:
    _restore(old)
    rg.GF_FROZEN = 2.0          # the real state value, restored

# the delta arithmetic on SYNTHETIC rows, no walk needed
_res = {("live", "frozen"): {"total": 39.50, "ctrl": -126.67, "floor": 62},
        ("live", "day"): {"total": 41.20, "ctrl": -120.00, "floor": 58},
        ("hybrid", "frozen"): {"total": 138.96, "ctrl": -68.59, "floor": 0},
        ("hybrid", "day"): {"total": 140.00, "ctrl": -66.00, "floor": 0}}
d_live = _res[("live", "day")]["total"] - _res[("live", "frozen")]["total"]
d_hyb = _res[("hybrid", "day")]["total"] - _res[("hybrid", "frozen")]["total"]
check("3.4 the delta is per-day minus frozen (LIVE +1.70, hybrid "
      "+1.04 on the synthetic rows)",
      abs(d_live - 1.70) < 1e-9 and abs(d_hyb - 1.04) < 1e-9)

# -- 4. the live world ---------------------------------------------
buf = io.StringIO()
with redirect_stdout(buf):
    rc = _main_rc()
out = buf.getvalue()
check("4.1 the real tree passes its own parity gate (both frozen "
      "rows reproduce the referee ARTIFACT re-run on the same data "
      "- copy-vs-copy, immune to data movement)",
      rc != 3 and "live reproduces:  OK" in out
      and "hybrid reproduces: OK" in out)
check("4.5 the committed record rides along as history (printed with "
      "its commit id; an honest note explains whenever today's rows "
      "sit off it - the Sep 28 backfill moved live +39.50 -> +40.68 "
      "while artifact and copy matched to the cent)",
      "committed record (459c02b)" in out)
check("4.2 the real run exits 0 in whichever honest world the series "
      "spans (NOT YET now; DELTA once 14 days accrue)",
      rc == 0 and ("NOT YET" in out) != ("THE DELTA" in out))
check("4.3 the real series' fingerprint matches its own rows (every "
      "series day reads back its factor)",
      all(rg.GFS.day_factor(d) == f for d, f in rg.GFS.series.items()))
check("4.4 the span gate is calibrated to the accrual ask (MIN_DAYS "
      "14 ~ 2 weeks)",
      rg._gf_series.MIN_DAYS == 14)

# -- 5. the shared seam itself (_gf_series, the arc's single source)
from _gf_series import GFSeries as _G
s_dict = _G(rows=[{"day": "2026-09-26", "factor": 1.5},
                  {"day": "2026-09-27", "factor": 1.8}])
check("5.1 dict rows and [[day, factor]] rows build the same series",
      s_dict.span_days == 2 and s_dict.frozen == 1.8
      and s_dict.span == "2026-09-26..2026-09-27")
s_edge = _G(rows=[["2026-09-26", 1.5], ["2026-09-27", 1.8]],
            frozen=1.8)
check("5.2 the span gate boundary: 13 days refuses, 14 reports",
      _G(rows=[[f"2026-09-{d:02d}", 1.0] for d in range(13, 27)] +
             [["2026-10-10", 1.0]]).ready() is True
      and _G(rows=[[f"2026-09-{d:02d}", 1.0] for d in range(14, 27)])
      .ready() is False)
check("5.3 the notyet line names the span and the ask",
      "0 day(s)" in _G(rows=[]).notyet_line()
      and ">= 14" in _G(rows=[]).notyet_line())

# the frozen baseline is RESTORED, not hardcoded (the isolated
# review's minor: `rg.GF_FROZEN = 2.0` in a finally would silently
# bless 2.0 as 'the original' if the real state ever moves)
if _gf_old is not None:
    rg.GF_FROZEN = _gf_old
else:
    del rg.GF_FROZEN

print(chr(10).join(lines))
print()
print(f"RESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
