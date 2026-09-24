"""THE MEASURED-BUDGET CONTRACT (Sep 23, the costdrivers WARN).

Pins run_battery.budget_for to its measured-slow-suite contract:

  1. The flat PER_ENTRY_TIMEOUT stays the default — never-measured suites
     and fresh clones (no lanes manifest) keep tight hang detection.
  2. A suite with a measured lane time earns
     max(override, MEASURED_BUDGET_MULT x mean), capped at
     MAX_MEASURED_BUDGET — in EVERY mode: costdrivers' honest ~280s run
     must never again WARN against a budget its own history contradicts.
  3. The override map keeps its floor (apptest's 900) and the cap bounds
     the ratchet (a suite slowing 10x re-baselines its budget — the cap
     is the ceiling on that loop).

All mean-dependent checks run against a SANDBOX manifest (the real
manifest is runtime state whose values move with the machine) — the
only live-manifest check is the presence one, skipped honestly when the
manifest is absent. Prints "RESULT: N passed, M failed"; fast by design.
"""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_battery as rb

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    mark = "PASS" if cond else "FAIL"
    print(f"{mark}  {n}" + (f"  ({why})" if why and not cond else ""))


print("== 1. constants: the dials exist and say what they mean ==")
check(1.1, rb.MEASURED_BUDGET_MULT == 3.0, "the multiplier dial moved")
check(1.2, rb.MAX_MEASURED_BUDGET == 1800, "the 30-min cap moved")
check(1.3, rb.PER_ENTRY_TIMEOUT == 420, "the flat default moved unexpectedly")

print("== 2. sandbox: the budget math, proven deterministically ==")
_mf = rb.LANES_MANIFEST
try:
    with tempfile.TemporaryDirectory(prefix="battbudget_") as td:
        sbx = Path(td) / "lanes.json"

        # 2.1 no manifest at all (fresh clone): flat default
        rb.LANES_MANIFEST = sbx   # absent file
        check(2.1, rb.budget_for("test_costdrivers") == rb.PER_ENTRY_TIMEOUT,
              "a missing manifest must fail-soft to the flat default")

        # 2.2 measured mean: MULT x mean wins over the flat default
        sbx.write_text(json.dumps(
            {"test_costdrivers": {"secs": 280.9, "runs": 5}}), encoding="utf-8")
        check(2.2, rb.budget_for("test_costdrivers") == 842,
              f"3x280.9 should be 842, got {rb.budget_for('test_costdrivers')}")

        # 2.3 the override keeps its floor: max(override, mult x mean)
        sbx.write_text(json.dumps(
            {"test_apptest": {"secs": 100, "runs": 2}}), encoding="utf-8")
        check(2.3, rb.budget_for("test_apptest") == 900,
              "a small mean must not LOWER the override's 900")

        # 2.4 the override can be raised: 3x400 > 900
        sbx.write_text(json.dumps(
            {"test_apptest": {"secs": 400, "runs": 2}}), encoding="utf-8")
        check(2.4, rb.budget_for("test_apptest") == 1200,
              "a grown mean must raise the override (the growth direction)")

        # 2.5 the cap bounds the ratchet
        sbx.write_text(json.dumps(
            {"test_apptest": {"secs": 700, "runs": 2}}), encoding="utf-8")
        check(2.5, rb.budget_for("test_apptest") == rb.MAX_MEASURED_BUDGET,
              "3x700 must cap at MAX_MEASURED_BUDGET")

        # 2.6 the WARN band regains its meaning: honest run << 50% of budget
        # (restore the costdrivers fixture — 2.5's manifest holds only apptest)
        sbx.write_text(json.dumps(
            {"test_costdrivers": {"secs": 280.9, "runs": 5}}), encoding="utf-8")
        check(2.6, 280.9 < 0.5 * rb.budget_for("test_costdrivers"),
              "the honest mean must sit under the WARN band again")
finally:
    rb.LANES_MANIFEST = _mf   # the real manifest, always restored

print("== 4. auto-chunk: the pause/resume contract (Sep 24) ==")
_rf = rb.RESUME_FILE
try:
    with tempfile.TemporaryDirectory(prefix="battchunk_") as td:
        rb.RESUME_FILE = Path(td) / "resume.json"

        # 4.1 the pause round-trips (mode key, chunk number, wall, rows)
        rb._resume_save("full:trend", "trend", 2,
                        [["test", "test_trendbridge", "PASS", 3.2, "12/12"]],
                        [], 615.0)
        _d = rb._resume_load()
        check(4.1, _d.get("mode") == "full:trend" and _d.get("chunk") == 2
              and _d.get("wall_acc") == 615.0
              and _d.get("rows")[0][1] == "test_trendbridge",
              "the pause must round-trip (mode, chunk, wall_acc, rows)")

        # 4.2 no pause file loads empty (fresh clone)
        rb.RESUME_FILE = Path(td) / "absent.json"
        check(4.2, rb._resume_load() == {}, "no pause file must load empty")

        # 4.3 a corrupt pause fails soft — never crashes the battery
        rb.RESUME_FILE = Path(td) / "resume.json"
        rb.RESUME_FILE.write_text("{broken", encoding="utf-8")
        check(4.3, rb._resume_load() == {},
              "a corrupt pause must fail soft to empty")

        # 4.4 a completed sweep retires the pause (file gone)
        rb.RESUME_FILE = Path(td) / "resume.json"
        rb._resume_save("full", "", 1, [], [], 10.0)
        rb._resume_clear()
        check(4.4, rb._resume_load() == {},
              "_resume_clear must remove the pause")

        # 4.5 the new flags parse (and never shift the positional filter)
        _f, _p = rb._parse_flags(["--chunk=90", "log", "", "trend"])
        check(4.5, _f == {"chunk": 90} and _p == ["log", "", "trend"],
              "--chunk must parse without shifting positionals")
        _f, _p = rb._parse_flags(["--fresh", "--chunk=0"])
        check(4.6, _f == {"fresh": True, "chunk": 0},
              "--fresh and --chunk=0 must parse")

        # 4.7 the default window is the 600s terminal reality
        check(4.7, rb.CHUNK_DEFAULT == 600, "the default chunk window moved")
finally:
    rb.RESUME_FILE = _rf   # the real pause file, always restored

# the pause/resume wiring lives inline in main() — pinned by source, the
# same way the drive census pins inline production logic
print("== 5. auto-chunk wiring: source pins on main() ==")
_src = Path("run_battery.py").read_text(encoding="utf-8")
check(5.1, 'if r[2] != "PAUSED"' in _src,
      "the paused entry's marker row must be dropped AND its name not "
      "counted done — it belongs to the next chunk")
check(5.2, "chunk_limit = 0" in _src,
      "the resumed tail must run without further pausing (fresh budget)")
check(5.3, '_rec["chunks"]' in _src and "_resume_clear()" in _src,
      "a completed chunked sweep retires the pause and stamps its chunk "
      "count on the one history record")
check(5.4, '(lane or "full")' in _src,
      "a chunked full run must keep the 'full' mode in its history record "
      "(baseline-comparable)")
check(5.5, 'paused = {} if lane == "fast"' in _src,
      "--fast must never pause (and leaves a waiting pause alone)")
check(5.6, "mode_key = (lane or" in _src and 'f":{_flt}"' in _src,
      "the pause must be keyed by mode+filter so a different command "
      "never hijacks another sweep's pause")
check(5.7, "return 2" in _src,
      "a paused invocation must exit rc=2 (no summary — the completed "
      "sweep assembles it)")
check(5.8, "if _saving:" in _src
      and '_saving = lane != "fast" and not _foreign_block' in _src,
      "resume saves must route through the single _saving dial: --fast "
      "never writes pause state, and a run facing a foreign pause never "
      "saves its own progress over the waiting sweep's state")

print("== 6. the scan-aware ledger guard (Sep 24) ==")
from datetime import datetime as _fromiso
_T0 = _fromiso.fromisoformat("2026-09-24T09:00:00-06:00").timestamp()
_T1 = _T0 + 600.0                        # a ten-minute entry window
_t0, _t1 = _T0, _T1
_A = "2026-09-24T09:00:00-06:00"          # added at the window's open
_LIVE = {"added": _A, "window_min": 120}   # expires 11:00, AFTER the run
_EXP = {"added": "2026-09-24T07:00:00-06:00", "window_min": 60}  # 08:00, before t0


def _w(entries):
    return json.dumps(entries)


def _acct(cid):
    return json.dumps({"contest_id": cid, "current_balance": 10000.0})


# 6.1 the enrichment pass-through still holds (additions never flag)
check(6.1, rb._guard_leak(
          {"contest_watch.json": _w({"A/USD": _LIVE}),
           "contest_account.json": _acct("x")},
          {"contest_watch.json": _w({"A/USD": _LIVE, "B/USD": _LIVE}),
           "contest_account.json": _acct("x")}, _t0, _t1) is None,
      "a live fire enriching the watch stays free")

# 6.2 THE HEADLINE: the scanner's exit removes a live row mid-suite ->
# a close receipt in the window exonerates it (the costdrivers shape)
check(6.2, rb._guard_leak(
          {"contest_watch.json": _w({"A/USD": _LIVE}),
           "contest_account.json": _acct("x")},
          {"contest_watch.json": _w({}),
           "contest_account.json": _acct("x")}, _t0, _t1,
          receipts={"A/USD"}) is None,
      "a vanished live entry WITH a close receipt is the scanner working")

# 6.3 the same vanish with NO receipt, window still open -> the leak
check(6.3, rb._guard_leak(
          {"contest_watch.json": _w({"A/USD": _LIVE}),
           "contest_account.json": _acct("x")},
          {"contest_watch.json": _w({}),
           "contest_account.json": _acct("x")}, _t0, _t1,
          receipts=set()) is not None,
      "a vanished live entry with no receipt is the 13:54 leak — flagged")

# 6.4 the PARTIAL wipe gap closes: other rows survive, the live one
# doesn't, no receipt -> still flagged (the old ->{}-only check missed it)
check(6.4, rb._guard_leak(
          {"contest_watch.json": _w({"A/USD": _LIVE, "B/USD": _LIVE}),
           "contest_account.json": _acct("x")},
          {"contest_watch.json": _w({"B/USD": _LIVE}),
           "contest_account.json": _acct("x")}, _t0, _t1,
          receipts=set()) is not None,
      "a partial wipe loses its receipt-less live entry — flagged even "
      "with survivors")

# 6.5 expiry exoneration: the window ran out during the suite (the
# scanner retires it — a long suite's legitimate mid-run shape)
check(6.5, rb._guard_leak(
          {"contest_watch.json": _w({"A/USD": _EXP}),
           "contest_account.json": _acct("x")},
          {"contest_watch.json": _w({}),
           "contest_account.json": _acct("x")}, _t0, _t1,
          receipts=set()) is None,
      "an entry whose window expired by the after-snapshot is quiet retirement")

# 6.6 the fresh-window receipt: new_contest stamps its id INSIDE the run
# window -> its documented wipe rides free, and the account id move is fine
check(6.6, rb._guard_leak(
          {"contest_watch.json": _w({"A/USD": _LIVE}),
           "contest_account.json": _acct("2026-09-20T09:00:00-06:00")},
          {"contest_watch.json": _w({}),
           "contest_account.json": _acct("2026-09-24T09:08:00-06:00")},
          _t0, _t1, receipts=set()) is None,
      "a contest opened mid-run (stamp inside the window) is the user's "
      "new_contest receipt — wipe and id move both free")

# 6.7 the phantom reset keeps flagging: id moved but the stamp predates
# the run (an old file restored, a rogue write — no receipt)
check(6.7, rb._guard_leak(
          {"contest_watch.json": _w({}),
           "contest_account.json": _acct("2026-09-09T13:40:07-06:00")},
          {"contest_watch.json": _w({}),
           "contest_account.json": _acct("2026-09-01T00:00:00-06:00")},
          _t0, _t1, receipts=set()) is not None,
      "a contest_id move with no fresh-window stamp is still the phantom reset")
# 6.8 the guard reads the REAL ledger when receipts are not handed in
# (the production path) — a receipt inside the window saves the entry
_rlf = rb.SCOUT / "contest_trades.jsonl"
_real = _rlf.read_bytes() if _rlf.exists() else None
try:
    _rlf.write_text(
        json.dumps({"pair": "A/USD", "sold_at": "2026-09-24T09:05:00-06:00"})
        + "\n", encoding="utf-8")
    check(6.8, rb._guard_leak(
              {"contest_watch.json": _w({"A/USD": _LIVE}),
               "contest_account.json": _acct("x")},
              {"contest_watch.json": _w({}),
               "contest_account.json": _acct("x")}, _t0, _t1) is None,
          "with no handed receipts the guard opens contest_trades.jsonl "
          "itself and the close record exonerates")
finally:
    if _real is None:
        _rlf.unlink(missing_ok=True)
    else:
        _rlf.write_bytes(_real)

# 6.9 the unreadable ledger yields NO receipts — fails toward flagging
check(6.9, rb._guard_leak(
          {"contest_watch.json": _w({"A/USD": _LIVE}),
           "contest_account.json": _acct("x")},
          {"contest_watch.json": _w({}),
           "contest_account.json": _acct("x")},
          _t0, _t1, receipts=None, ) is not None,
      "the real ledger holds no window receipt here — flag (fail toward flagging)")

# 6.10 the wiring: both call sites hand the epoch window in
check(6.10, "_gs, _g_t0 = _guard_snap(), time.time()" in _src
      and _src.count("_guard_leak(_gs, _guard_snap(), _g_t0, time.time())") == 2,
      "both test and diag loops capture the snapshot epoch and hand the "
      "run window to the guard")
check(6.11, "time.sleep(0.25)" in _src and "for _try in range(3)" in _src,
      "the snapshot read retries through the scanner's write window "
      "before calling a file unreadable")

print("== 3. live: the real manifest speaks when it exists ==")
_live = rb._lanes_load().get("test_costdrivers")
if _live and _live.get("secs"):
    check(3.1, rb.budget_for("test_costdrivers")
          > rb.PER_ENTRY_TIMEOUT,
          "costdrivers is measured — its budget must exceed the flat 420")
    check(3.2, _live["secs"] < 0.5 * rb.budget_for("test_costdrivers"),
          "the live mean must sit under the WARN band (the original sin)")
else:
    print("SKIP  3.x  (no measured costdrivers in the manifest — fresh "
          "clone; checks 2.2-2.6 carry the contract)")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
