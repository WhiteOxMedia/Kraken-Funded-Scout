"""THE DIRECTIVE'S PLATE, WIRED (Oct 7, the 12:00-verification catch).

The rotation-mode directive (9a8a353) capped FIRING zero-realized
rotations at 55, named the failure shape in the lesson, and taught the
plate an amber grade — "ZERO REALIZED — the directive cap holds". The
12:00-verification rehearsal caught the wiring gap: evaluate_rotation
applied the cap and banked the lesson, but the PERSISTED history row
never carried empty_fire / capped_zero_zero — the plate grades from
_last.get(...) on exactly those rows, so every live row read flagless
(proven: all five Oct 7 rows show null) and the amber grade could
never render from live data. A capped rotation at 12:00 would have
graded "CAREFUL — money slipped" (red) — the directive's verdict
masked by its own persistence.

Pins:
  1. THE REHEARSAL: a firing rotation, realized <= $0, <= the crumb
     left -> score 55, the flag set, the lesson names the failure
     shape (the cap itself, green since 9a8a353)
  2. the PERSISTED row carries both flags (the wiring cure)
  3. the REAL plate renders ZERO REALIZED in amber from the persisted
     sandbox state (the grade, end to end)
  4. the silent-rotation twin: empty_fire persists and the plate
     renders SILENT ROTATION (the same gap, same cure)

Hermetic: module-function injection (sold/popups/suppressions/fired),
sandbox load/save, stubbed contest_account, ladder ingest no-op,
speak=False. The plate renders from the SANDBOX state via the
STATE_FILE rebind — production discipline_state.json never touched.
"""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import discipline as dc

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not ok else ""))


MST = timezone(timedelta(hours=-6))
start = datetime(2026, 10, 8, 8, 0, tzinfo=MST)
end = datetime(2026, 10, 8, 12, 0, tzinfo=MST)


class FakeExchange:
    def fetch_ohlcv(self, *a, **k):
        return []

    def fetch_ticker(self, pair):
        return {"last": 1.0}


class StubAccount:
    def load_state(self):
        return {"current_balance": 10022.29}

    def daily_pace_usd(self, acct):
        return 240.0


def _run(fired_pairs):
    saved = []
    sb = Path(tempfile.mkdtemp(prefix="dpl_"))
    _real = {k: getattr(dc, k) for k in
             ("_sold_in_rotation", "_popups_in_rotation",
              "_suppressions_in_rotation", "_fired_pairs", "load_state",
              "save_state", "_worth_ladder_ingest")}
    try:
        dc._sold_in_rotation = lambda s, e: [
            {"pair": "LOSSY/USD", "sold_at": start.isoformat(),
             "entry": 1.0, "exit": 0.997, "size": 1000.0, "pnl_net": -3.0,
             "goal_pct": 2.0, "_ts": start}]
        dc._popups_in_rotation = lambda s, e: []
        dc._suppressions_in_rotation = lambda s, e: {}
        dc._fired_pairs = lambda s, e, voice_file=None: fired_pairs
        dc.load_state = lambda: {"weights": dict(dc.DEFAULT_WEIGHTS),
                                 "history": [], "lessons": [],
                                 "burial_ledger": {}}
        dc.save_state = lambda st: saved.append(st)
        dc._worth_ladder_ingest = lambda result: None
        sys.modules["contest_account"] = StubAccount()
        res = dc.evaluate_rotation(FakeExchange(), "08-12", when=end,
                                   speak=False, watch_file=str(sb / "w.json"))
        row = (saved[-1]["history"] or [{}])[-1]
    finally:
        for k, v in _real.items():
            setattr(dc, k, v)
        sys.modules.pop("contest_account", None)
    return res, row, saved


res, row, _ = _run({"LOSSY/USD"})

# 1. THE CAP ITSELF (the shape: firing, realized <= 0, crumb left)
check("1.1 firing + realized $-3 + $0 left -> score capped at 55, "
      "capped_zero_zero set",
      res.get("score") == 55.0
      and res.get("capped_zero_zero") is True,
      str({"score": res.get("score"),
           "flag": res.get("capped_zero_zero")}))
check("1.2 the lesson NAMES the failure shape (never masks it)",
      "directive cap holds" in (res.get("lesson") or "")
      and "failure shape" in (res.get("lesson") or ""),
      str(res.get("lesson"))[:160])

# 2. THE PERSISTED ROW CARRIES THE FLAGS (the wiring cure — red pre-fix)
check("2.1 the persisted history row carries capped_zero_zero=True",
      row.get("capped_zero_zero") is True, str(row.get("capped_zero_zero")))
check("2.2 the persisted history row carries empty_fire=False (a FIRING "
      "rotation, distinct from the silent cap)",
      row.get("empty_fire") is False, str(row.get("empty_fire")))

# 3. THE REAL PLATE renders the amber grade from the persisted sandbox
#    state (app reads discipline_state via the same module — the
#    STATE_FILE rebind points it at the rehearsal's row)
sb = Path(tempfile.mkdtemp(prefix="dpl_plate_"))
sf = sb / "discipline_state.json"
sf.write_text(json.dumps({"history": [row],
                          "lessons": [{"ts": row.get("ts"),
                                       "lesson": res.get("lesson")}]}),
              encoding="utf-8")
dc.STATE_FILE = sf
import app as _app                                        # noqa: E402
html = _app._discipline_plate()
check("3.1 the plate grades ZERO REALIZED in amber (the directive's "
      "verdict, end to end)",
      "ZERO REALIZED" in html and "#ffc36b" in html
      and "CAREFUL" not in html)

# 4. THE SILENT TWIN: empty_fire persists and grades SILENT ROTATION
res2, row2, _ = _run(set())          # nothing fired -> the empty-fire cap
sf.write_text(json.dumps({"history": [row2], "lessons": []}),
              encoding="utf-8")
dc.STATE_FILE = sf
html2 = _app._discipline_plate()
check("4.1 the silent twin: empty_fire persists and the plate grades "
      "SILENT ROTATION",
      row2.get("empty_fire") is True and "SILENT ROTATION" in html2,
      str(row2.get("empty_fire")))

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
