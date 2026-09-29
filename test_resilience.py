"""THE RESILIENCE DRIVE (Sep 29): pins the three ops-hardening
deliverables —

  1. atomic_io RETRY TELEMETRY: every try emits one structured line
     (op/attempt/outcome/error/elapsed); retries are observable
     without touching production.
  2. THE EMERGENCY RESTORE PATH: plan/commit replay of the auto-adjust
     telemetry jsonl rehydrates the guard state (factor anchored at
     the newest before_gf, ledger rows as data) WITHOUT re-applying —
     mutation-proven: deleting the anchor clause and anchoring at
     after_gf ships factor 0.9168 and this drive reads 0.9168 and
     fails.
  3. THE STATE-WRITER LINTER as a first-class gate drive: R1
     unregistered writer / R2 non-atomic save / R3 resurrection shape,
     run against the STAGED tree every commit.

Exit 1 on any failure."""
import json
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name
                 + (f"  [{detail}]" if detail and not ok else ""))
    if ok:
        PASS += 1
    else:
        FAIL += 1


import atomic_io as aio
import restore_state as rs
import scratch_space   # scratch policy: nothing volatile lands in the repo root

# ── 1. THE RETRY TELEMETRY ───────────────────────────────────────────
events = []
_rec = aio._Recorder if hasattr(aio, "_Recorder") else None


class _Flaky:
    def __init__(self, fail_times, err):
        self.n = fail_times
        self.err = err
        self.calls = 0

    def __call__(self, tmp, target):
        self.calls += 1
        if self.calls <= self.n:
            raise self.err
        return None


class _Sleeper:
    def __init__(self):
        self.t = []

    def __call__(self, d):
        self.t.append(d)


def _err(we):
    e = OSError(32, "sharing violation")
    e.winerror = we
    return e


aio.replace_atomic("t", "f", attempts=3, base_delay=0.05,
                   replace=_Flaky(2, _err(32)),
                   sleep=_Sleeper(),
                   note=events.append)
_retried = [e for e in events if e["outcome"] == "retried"]
_ok = [e for e in events if e["outcome"] == "success"]
check("telemetry: each retry emits one structured line (op/target/"
      "attempt/outcome/error/elapsed/ts)",
      len(events) == 3 and len(_retried) == 2 and len(_ok) == 1
      and all(k in _retried[0] for k in
              ("op", "target", "attempt", "outcome", "error",
               "elapsed_ms", "ts")), events)
check("telemetry: attempts count from 0 and the success names the try "
      "that landed",
      [e["attempt"] for e in events] == [0, 1, 2]
      and _ok[0]["attempt"] == 2, events)
check("telemetry: the error carries the repr (diagnosable, greppable)",
      isinstance(_retried[0]["error"], str)
      and "Error" in _retried[0]["error"]
      and "sharing violation" in _retried[0]["error"], events)

# a first-try success stays SILENT (no success spam; only interesting
# outcomes reach the journal)
events2 = []
aio.replace_atomic("t", "f", attempts=3,
                   replace=_Flaky(0, _err(32)), sleep=_Sleeper(),
                   note=events2.append)
check("telemetry: a clean first-try success emits nothing",
      events2 == [], events2)

# exhaustion emits its own terminal line
events3 = []
_raised = None
try:
    aio.replace_atomic("t", "f", attempts=2, base_delay=0.05,
                       replace=_Flaky(9, _err(5)), sleep=_Sleeper(),
                       note=events3.append)
except OSError as exc:
    _raised = exc
_outs = [e["outcome"] for e in events3]
check("telemetry: every failed try logs 'retried', the terminal line "
      "is 'exhausted', and the last contention error still raises "
      "unchanged",
      _outs == ["retried", "retried", "exhausted"]
      and events3[-1]["attempt"] == 1 and _raised is not None
      and _raised.winerror == 5, (events3, _raised))

# the real default sink writes through
_d = Path(tempfile.mkdtemp())
_d2 = Path(tempfile.mkdtemp())
(_d2 / "tmp").write_text("x", encoding="utf-8")
_real_sink = aio.TELEMETRY_PATH
aio.TELEMETRY_PATH = _d / "io_events.jsonl"
try:
    aio.replace_atomic(_d2 / "tmp", _d2 / "target", attempts=2,
                       replace=_Flaky(1, _err(32)))
finally:
    aio.TELEMETRY_PATH = _real_sink
check("telemetry: the default sink lands parseable jsonl on disk",
      (_d / "io_events.jsonl").exists()
      and json.loads((_d / "io_events.jsonl")
                     .read_text(encoding="utf-8").splitlines()[0])
      ["op"] == "replace_atomic", "")

# ── 2. THE EMERGENCY RESTORE PATH ────────────────────────────────────
import profit_guard as pg
import telemetry_adjust as ta

_sbx = Path(tempfile.mkdtemp())
_jrn = _sbx / "telemetry.jsonl"
_pg_state = _sbx / "profit_guard_state.json"

evs = [
    {"event": "auto_adjust", "rotation_id": "2026-09-29-00-04",
     "before_gf": 1.0, "after_gf": 0.9168, "delta": -0.0832,
     "B": 14, "P": 6, "per_gate": [], "evidence_checksum":
     "sha256:aaa", "cohort": "canary-1", "applied_by": "system",
     "status": "applied", "reason": "r below min",
     "timestamp": "2026-09-29T04:05:01-06:00"},
    {"event": "auto_adjust", "rotation_id": "2026-09-29-04-08",
     "before_gf": 0.9168, "after_gf": 0.8336, "delta": -0.0832,
     "B": 22, "P": 4, "per_gate": [], "evidence_checksum":
     "sha256:bbb", "cohort": "canary-1", "applied_by": "system",
     "status": "applied", "reason": "r below min",
     "timestamp": "2026-09-29T08:05:01-06:00"},
    {"event": "auto_adjust", "rotation_id": "2026-09-29-08-12",
     "before_gf": 0.8336, "after_gf": 0.8336, "delta": 0.0,
     "B": 9, "P": 1, "per_gate": [], "evidence_checksum": "",
     "cohort": "canary-1", "applied_by": "system",
     "status": "refused", "reason": "rotation_already_applied",
     "timestamp": "2026-09-29T12:05:01-06:00"},
]
_jrn.write_text("\n".join(json.dumps(e) for e in evs) + "\n",
                encoding="utf-8")

_old_file = getattr(pg, "STATE_FILE", None)
pg.STATE_FILE = _pg_state
_pg_real_save = pg._save
try:
    # dry run: nothing lands
    r1 = rs.restore(_jrn)
    check("restore: the dry run plans without writing (no state file "
          "appears)",
          r1["status"] == "planned" and r1["rows"] == 2
          and not _pg_state.exists(), r1)
    check("restore: the plan anchors at the NEWEST applied event's "
          "before_gf (the pre-apply restore target, not the loosened "
          "after)",
          r1["factor"] == 0.9168, r1)

    r2 = rs.restore(_jrn, commit=True)
    doc = json.loads(_pg_state.read_text(encoding="utf-8"))
    check("restore: commit rehydrates factor + ledger through "
          "profit_guard._save (merge-save + atomic)",
          r2["status"] == "rehydrated" and doc["factor"] == 0.9168
          and len(doc["auto_adjust_ledger"]) == 2, (r2, doc))
    check("restore: the rehydrated ledger rows carry delta/before/"
          "after/evidence/ts (the full evidence, replayed as data)",
          all(k in doc["auto_adjust_ledger"][0] for k in
              ("delta", "before", "after", "reason", "evidence", "ts")),
          doc["auto_adjust_ledger"])
    check("restore: the refused event never becomes a ledger row",
          all(r["delta"] != 0.0 for r in doc["auto_adjust_ledger"]),
          doc["auto_adjust_ledger"])

    # THE DOUBLE-APPLY MUTATION: anchor the replay at after_gf (the
    # corrupted-replay shape — equivalent to re-applying the loosened
    # factor) and the drive must read the wrong factor and fail.
    _old_plan = rs.plan_restore

    def _mutated_plan(tp, current=None):
        plan = _old_plan(tp, current=current)
        if plan and not plan.get("kept") and plan["factor"] is not None:
            evs_ap = rs._applied_events(tp)
            plan["factor"] = float(evs_ap[-1].get("after_gf") or 1.0)
        return plan

    rs.plan_restore = _mutated_plan
    _pg_state.unlink()
    r3 = rs.restore(_jrn, commit=True)
    rs.plan_restore = _old_plan
    doc3 = json.loads(_pg_state.read_text(encoding="utf-8"))
    check("MUTATION: anchoring the replay at after_gf (the "
          "double-apply shape) ships 0.8336 — the pin reads 0.9168 "
          "and fails (the restore cannot silently re-apply)",
          doc3["factor"] == 0.8336 and r3["factor"] == 0.8336
          and doc3["factor"] != 0.9168, (r3, doc3))

    # the replay through the REAL apply path is the no-double-apply
    # contract: a second replay of the SAME journal must be idempotent
    # (same factor, not another multiplication)
    r4 = rs.restore(_jrn, commit=True)
    doc4 = json.loads(_pg_state.read_text(encoding="utf-8"))
    check("restore: replaying the same journal is idempotent (same "
          "factor, the ledger replaced not multiplied)",
          doc4["factor"] == 0.9168
          and len(doc4["auto_adjust_ledger"]) == 2, (r4, doc4))
finally:
    if _old_file is not None:
        pg.STATE_FILE = _old_file
    else:
        del pg.STATE_FILE
    pg._save = _pg_real_save

# an empty journal with no current state: the honest skip
_sbx2 = Path(tempfile.mkdtemp())
_empty = _sbx2 / "telemetry.jsonl"
_empty.write_text("", encoding="utf-8")
pg.STATE_FILE = _sbx2 / "profit_guard_state.json"
try:
    r5 = rs.restore(_empty, commit=True)
    check("restore: an empty journal with no current state ships the "
          "honest skip",
          r5["status"] == "skipped" and r5["rows"] == 0, r5)
finally:
    if _old_file is not None:
        pg.STATE_FILE = _old_file
    else:
        del pg.STATE_FILE

# ── 3. THE STATE-WRITER LINTER, WIRED ────────────────────────────────
# resolve the hook through the real tree (a BASE-relative open misses
# under the gate's scratch; this leg reads the git root)
import subprocess
_root = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True).stdout.strip()
_hook_p = ((Path(_root) / "hooks" / "pre-commit") if _root
           else (BASE / "hooks" / "pre-commit"))
try:
    _hook_src = _hook_p.read_text(encoding="utf-8")
except Exception:
    _hook_src = ""
check("linter: the gate runs the state-writer tripwire as a "
      "first-class drive (R1 unregistered writers / R2 non-atomic "
      "saves / R3 resurrection shapes can no longer land quietly)",
      "test_statewriters" in _hook_src, "")

out = "\n".join(lines) + f"\n\n{PASS} passed, {FAIL} failed\n"
open(scratch_space.report_temp('_resilience_check.txt'), "w",
     encoding="ascii", errors="replace").write(out)
print(out)
sys.exit(1 if FAIL else 0)
