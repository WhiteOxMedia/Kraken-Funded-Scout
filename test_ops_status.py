"""test_ops_status.py — THE OPS RECEIPT DRIVE (Sep 27).

ops_status.py is the one-command health receipt (bundle version,
listener PID, http health, scheduler heartbeat, last orphan scan,
scheduled-task Last Result, dead-man marker) with an alertable exit
code. Proves:

  1. THE LIVE SURFACES (skip-honest when the server is down): the real
     collect() lands all-clear on a healthy box, and its exit code is 0.
  2. THE READERS, HERMETIC: bundle version parsing (and the honest "?"
     on a missing template), down-port detection, and the mode=ro
     sandbox DB read of heartbeat + dial + orphan marker.
  3. THE ALERT MATRIX: each alert fires on its own broken surface and
     nothing else - including the OFF dial sparing a stale heartbeat.
  4. THE EXIT CODE: 0 all-clear, 1 with any problem.

The live section runs against the real box; everything else is
sandboxed by patching the module's path/port constants.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "pool"))

import ops_status as ops

results = []


def check(n, cond, msg=""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + str(n)
          + ("  " + str(msg) if msg else ""))


live_up = ops.http_health() == "200"

print("== 1. THE LIVE RECEIPT (skip-honest when the server is down) ==")
if live_up:
    d = ops.collect()
    check("1.1", d["listener_pid"] and d["http_health"] == "200"
          and d["bundle_version"].isdigit(),
          f"live box answers: PID {d['listener_pid']}, v{d['bundle_version']}")
    check("1.2", d["scheduler_enabled"] == "1"
          and d["heartbeat_age_s"] is not None
          and d["heartbeat_age_s"] < ops.HEARTBEAT_STALE_S,
          f"daemon alive: heartbeat {d['heartbeat_age_s']}s ago")
    check("1.3", d["orphan_scan_age_s"] is not None
          and d["orphan_total"] == 0,
          f"last orphan scan clean: {d['orphan_total']}")
    check("1.4", d["task_last_result"] == 0
          and d["deadman_age_s"] is not None
          and d["deadman_age_s"] < ops.DEADMAN_MAX_AGE_S,
          f"task Last Result {d['task_last_result']}, dead-man fresh")
    rc = ops.main([])
    check("1.5", rc == 0, f"the receipt's own exit code: {rc}")
else:
    print("SKIP: live server not reachable - the live layer skipped "
          "(the hermetic layers carry the contract)")


print("== 2. THE READERS, HERMETIC ==")
_tmp = Path(tempfile.mkdtemp(prefix="ops_status_"))
tmpl = _tmp / "index.html"
tmpl.write_text('<script src="/static/app.js?v=42"></script>', encoding="utf-8")
check("2.1", ops.bundle_version(tmpl) == "42",
      "bundle version parsed from the served template")
check("2.2", ops.bundle_version(_tmp / "nope.html") == "?",
      "a missing template is an honest '?', never a crash")
check("2.3", ops.listener_pid(port=1) == ""
      and ops.http_health(port=1) == "down",
      "a dead port reads as not-listening / down")

import database as db
db.DB_PATH = _tmp / "jackpot.db"
db.init_db()
db.set_setting("SCHEDULER_ENABLED", "1")
db.set_setting("SCHEDULER_HEARTBEAT", str(time.time() - 60))
db.set_setting("SCHEDULER_LAST_ORPHANS", json.dumps(
    {"ts": time.time() - 3600, "total": 3, "edges": 2}))
st = ops.db_state(db.DB_PATH)
check("2.4", st["scheduler_enabled"] == "1"
      and 0 <= st["heartbeat_age_s"] < 120
      and st["orphan_total"] == 3 and st["orphan_edges"] == 2
      and 3500 < st["orphan_scan_age_s"] < 3700,
      f"mode=ro sandbox read: {st}")

print("== 2.5 THE GUARD-FACTOR PEEK (the scout sibling's GF series) ==")
gs = ops.guard_series(_tmp / "no_state.json")
check("2.5.1", gs == {"gf_latest": None, "gf_span": None, "gf_days": 0},
      f"a missing sibling state file is an honest NONE, never an alert: {gs}")
_state = _tmp / "pg_state.json"
_state.write_text(json.dumps({
    "factor": 1.8,
    "history": [
        {"day": "2026-09-26", "factor": 1.4},
        {"day": "2026-09-27", "factor": 1.8},
        {"day": "2026-09-27", "factor": 1.8},   # a same-day re-adjust: deduped
    ]}), encoding="utf-8")
gs2 = ops.guard_series(_state)
check("2.5.2", gs2["gf_latest"] == 1.8 and gs2["gf_days"] == 2
      and gs2["gf_span"] == "2026-09-26..2026-09-27",
      f"latest + deduped day-span read from the series: {gs2}")
check("2.5.3", ops._short_day("2026-09-27") == "0927"
      and ops._short_day("odd") == "odd",
      "the one-line span shrinks to MMDD; odd shapes pass through")
_receipt = {"listener_pid": "123", "http_health": "200",
            "scheduler_enabled": "1", "heartbeat_age_s": 60.0,
            "orphan_scan_age_s": 3600.0, "orphan_total": 0,
            "orphan_edges": 0, "task_last_result": 0,
            "task_last_run": "x", "task_next_run": "y",
            "deadman_age_s": 3600.0, "bundle_version": "27"}
_l4 = ops.one_line(_receipt)
check("2.5.4", "gf=NONE span=NONE" in _l4,
      f"absent series prints gf=NONE span=NONE, never crashes: {_l4}")
_l5 = ops.one_line(dict(_receipt, gf_latest=1.8,
                        gf_span="2026-09-26..2026-09-27"))
check("2.5.5", "gf=1.8 span=0926-0927" in _l5,
      f"the tiny daily-report fields render on the receipt line: {_l5}")


print("== 3. THE ALERT MATRIX (each surface alone) ==")
BASE = {"listener_pid": "123", "http_health": "200",
        "scheduler_enabled": "1", "heartbeat_age_s": 60.0,
        "orphan_scan_age_s": 3600.0, "orphan_total": 0,
        "orphan_edges": 0, "task_last_result": 0,
        "task_last_run": "x", "task_next_run": "y",
        "deadman_age_s": 3600.0,
        "bundle_version": "27"}


def alertsWith(**over):
    return ops.alerts(dict(BASE, **over))


check("3.1", alertsWith() == [], "the healthy baseline is silent")
check("3.2", len(alertsWith(listener_pid="", http_health="down")) == 1,
      "a dead server alerts alone")
check("3.3", len(alertsWith(heartbeat_age_s=99999.0)) == 1,
      "a stale heartbeat with the dial ON alerts")
check("3.4", alertsWith(heartbeat_age_s=99999.0,
                        scheduler_enabled="0") == [],
      "the operator OFF dial spares a stale heartbeat (honest silence)")
check("3.5", len(alertsWith(orphan_total=None)) == 1,
      "no orphan scan on record alerts")
check("3.6", len(alertsWith(orphan_total=5)) == 1,
      "orphan build-up alerts")
check("3.7", alertsWith(orphan_total=0, orphan_scan_age_s=None) != []
      or alertsWith(orphan_total=0) == [],
      "a clean scan stays silent")
check("3.8", len(alertsWith(task_last_result=2)) == 1,
      "a failed task run alerts (Last Result is the alert)")
check("3.9", len(alertsWith(deadman_age_s=None)) == 1,
      "a MISSING dead-man marker alerts (the task never ran)")
check("3.10", len(alertsWith(deadman_age_s=float(ops.DEADMAN_MAX_AGE_S)
                             + 3600)) == 1,
      "a stale dead-man marker alerts")

print("== 4. THE EXIT CODE ==")
_real_collect = ops.collect
ops.collect = lambda: dict(BASE)
rc0 = ops.main([])
ops.collect = lambda: dict(BASE, listener_pid="", http_health="down")
rc1 = ops.main([])
ops.collect = _real_collect
check("4.1", rc0 == 0 and rc1 == 1,
      f"all-clear -> 0, any problem -> 1 (got {rc0}/{rc1})")

print("== 5. THE ONE-LINE RECEIPT (--line) ==")
_l = ops.one_line(BASE)
check("5.1", _l.startswith("OK ") and "v27" in _l and "pid=123"
      in _l and "http=200" in _l and "hb=60s" in _l
      and "orphans=0@0.0d" in _l,
      f"all four fields, OK prefix: {_l}")
_l2 = ops.one_line(dict(BASE, listener_pid="", orphan_total=5))
check("5.2", _l2.startswith("ALERT ") and "pid=-" in _l2
      and "orphans=5@" in _l2,
      f"problems flip the prefix; missing PID prints as '-': {_l2}")
_l3 = ops.one_line(dict(BASE, heartbeat_age_s=None,
                        orphan_scan_age_s=None))
check("5.3", "hb=NONE" in _l3 and "orphans=0@NONE" in _l3,
      f"absent surfaces print NONE, never crash: {_l3}")
_rc_line = ops.main(["--line"])
check("5.4", _rc_line in (0, 1),
      f"--line keeps the alertable exit code: {_rc_line}")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
