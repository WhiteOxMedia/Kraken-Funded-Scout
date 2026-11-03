"""THE GATE-EV ROTATION-AWARENESS DRIVE (Oct 3, red-first).

gate_ev_table.py's 7d window read ONLY the live popup_suppressions.jsonl
— but the ledger rotates ~daily (SUPPRESSION_LOG_MAX lines), so "the
week" silently re-baselined to whatever the live file still held
(test_whyecon's own Oct-1 re-aim comment is the witness: the ledger
held ~ONE DAY and the historical burst had rotated out). A window that
silently shrinks is a study that lies by omission: every re-run priced
a different, unstated population and printed it as the same "7d".

THE FIX (the pergate_calibration.py pattern, Oct 2): the tool reads the
UNION — the live ledger plus every scratch/_supp_*.jsonl archive,
identical raw lines collapsed, rows ts-sorted into one chronology —
and states its COVERAGE (the days the surviving rows actually span),
so a two-day survivor can never masquerade as a week. The censoring
horizon stays LIVE-ONLY on purpose: candles for a row's outcome window
must exist NOW, and old archives' rows already satisfy that or drop
out honestly as no-coverage.

Pins (the module is loaded by path — scratch/ has no package init):
  1. the union read + collapse + sort, on sandboxed paths
  2. coverage truth: the scope names the union and the days the
     surviving rows actually span; the old live-only scope is gone
  3. the windows bound candidates by TIME, not by which file carried
     them — and first-seen dedupe is CHRONOLOGICAL (the union's ts
     order decides, never file order)
  4. panel400 keeps the live-only recipe (the panel's own definition)

Run: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe test_gateevtable.py
"""
import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS  " if ok else "FAIL  ") + name
          + (f"  [{detail}]" if detail and not ok else ""), flush=True)


_spec = importlib.util.spec_from_file_location(
    "gate_ev_table", BASE / "scratch" / "gate_ev_table.py")
gev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gev)

_old = (gev.SUPP, gev.ARCH_DIR, gev.DB, gev.OUT)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    gev.DB = root / "ohlcv_cache.db"          # never touched by these pins
    gev.OUT = root / "gate_ev_table.txt"
    live = root / "popup_suppressions.jsonl"
    arch = root / "archives"
    arch.mkdir()
    gev.SUPP = live
    gev.ARCH_DIR = arch

    # 48h ago / now / 5d ago, in the scanner's fixed-offset shape
    now = datetime.now().astimezone()
    t_now = now.isoformat(timespec="seconds")
    t_48h = (now - timedelta(hours=47)).isoformat(timespec="seconds")
    t_5d = (now - timedelta(days=5)).isoformat(timespec="seconds")
    _row = lambda ts, pair, gate, q=70.0: json.dumps(
        {"ts": ts, "pair": pair, "gate": gate, "quick": q, "detail": "x"})

    # archive: holds ONLY the 5-day-old burial — the live ledger has
    # rotated past it; the old code could never see this row in a 7d run
    (arch / "_supp_deadbeef.jsonl").write_text(
        _row(t_5d, "OLD/USD", "tide-out") + "\n"
        # an EARLIER copy of the live file's MID/USD stale-move refusal
        # (same pair+gate, older stamp — the funnel re-records): the
        # union's chronological first-seen must pick THIS one for 7d
        + _row(t_5d, "MID/USD", "stale-move", q=71.5) + "\n",
        encoding="utf-8")
    # live: one current row, one 48h row, and a DUPLICATE of the
    # archive's row (identical raw line — must collapse)
    live.write_text(
        _row(t_48h, "MID/USD", "stale-move") + "\n"
        + _row(t_now, "NEW/USD", "tide-out") + "\n"
        + _row(t_5d, "OLD/USD", "tide-out") + "\n",
        encoding="utf-8")

    # 1. the union sees the archive-only row inside 7d (the rotation
    #    fix), and the duplicate collapses (one OLD/USD candidate)
    cands, scope = gev.load_candidates("7d")
    pairs7 = sorted(c["pair"] for c in cands)
    check("1.1 the 7d union reads the archive-only row (rotation-aware)",
          "OLD/USD" in pairs7, (pairs7, scope))
    check("1.2 identical raw lines collapse (OLD/USD appears once)",
          pairs7.count("OLD/USD") == 1, pairs7)

    # 2. coverage truth: the scope states the union and the days — the
    #    old live-only 'from popup_suppressions.jsonl' scope is gone
    check("2.1 the scope states the coverage truth (union, day span, "
          "row count — never 'the week' when it is not)",
          "popup_suppressions.jsonl" not in scope
          and "union" in scope and "days" in scope, scope)
    days = sorted({r["_t"].astimezone().strftime("%Y-%m-%d")
                   for r in gev._ledger_rows()})
    check("2.2 the coverage days are the rows' actual days (3 spanned "
          "days for this fixture)", len(days) == 3, days)

    # 3. the windows bound candidates by TIME, not by file; dedupe is
    #    chronological
    cands48, scope48 = gev.load_candidates("48h")
    pairs48 = sorted(c["pair"] for c in cands48)
    check("3.1 the 48h window still excludes the 5-day-old archive row "
          "(the cutoff is TIME — archives extend depth only as far as "
          "the window says)",
          "OLD/USD" not in pairs48 and "NEW/USD" in pairs48,
          (pairs48, scope48))
    mid7 = next(c for c in cands if c["pair"] == "MID/USD")
    check("3.2 first-seen dedupe is CHRONOLOGICAL: the 7d candidate for "
          "MID/USD is the union's EARLIEST copy (the archive's 5-day "
          "row), never the live file's newer one — file order is dead",
          mid7["t"] == datetime.fromisoformat(t_5d)
          and mid7["quick"] == 71.5, mid7)

    # 4. panel400 keeps the LIVE-only recipe (the panel's own
    #    definition — archives would change the panel's population):
    #    its MID/USD candidate must be the LIVE copy (48h, quick 70),
    #    never the archive's older 5-day row (quick 71.5) — the one
    #    discriminating read between live-only and union
    candsP, scopeP = gev.load_candidates("panel400")
    midP = next(c for c in candsP if c["pair"] == "MID/USD")
    check("4.1 panel400 stays live-only: its MID/USD candidate is the "
          "LIVE copy, not the archive's older row (the panel's "
          "population is defined by the live ledger)",
          midP["t"] == datetime.fromisoformat(t_48h)
          and midP["quick"] == 70.0
          and "panel" in scopeP and "live" in scopeP,
          (midP, scopeP))

fails = [r for r in results if not r[1]]
import scratch_space
(scratch_space.report_temp("_gateevtable_check.txt")).write_text(
    "\n".join(f"{'PASS' if ok else 'FAIL'}  {name}"
              + (f"  [{d}]" if d and not ok else "")
              for name, ok, d in results)
    + f"\n\nRESULT: {len(results) - len(fails)} passed, {len(fails)} failed\n",
    encoding="utf-8")
print(f"\nRESULT: {len(results) - len(fails)} passed, {len(fails)} failed")


def main():
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    sys.exit(main())
