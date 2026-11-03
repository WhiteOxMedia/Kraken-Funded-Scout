"""THE TELEMETRY DRIVE (task 4): every event matches the schema's
required fields, the stream is append-only, the alert payload carries
the spec's four fields, and the bridge turns real auto_adjust records
into events. All on a sandboxed telemetry path."""
import json
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import auto_adjust as aa
import telemetry_adjust as ta

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


schema = json.loads((BASE / "observability" / "auto_adjust_schema.json")
                    .read_text(encoding="utf-8"))
required = set(schema["required"])
pgate_req = set(schema["properties"]["per_gate"]["items"]["required"])

ev = ta.make_event("2026-09-28-16-20", 2.0, 1.92, -0.08, 14, 6,
                   [{"gate": "deep-veto", "B_g": 8, "P_g": 4, "r_g": 0.5}],
                   "sha256:" + "a" * 64)
check("the event carries every schema-required field",
      required <= set(ev), sorted(required - set(ev)))
check("the per-gate rows carry the schema's per-gate fields",
      pgate_req <= set(ev["per_gate"][0]), ev["per_gate"])
check("the payer-rate rides as r (6/14)",
      abs(ev["r"] - 6 / 14) < 1e-9, ev["r"])
check("the negative delta is preserved (loosening is the story)",
      ev["delta"] == -0.08 and ev["after_gf"] < ev["before_gf"], ev)
check("the evidence checksum matches the sha256: shape",
      ev["evidence_checksum"].startswith("sha256:"), ev["evidence_checksum"])
check("cohort and applied_by default to the canary posture",
      ev["cohort"] == "canary-1" and ev["applied_by"] == "system", ev)

# the spec's exact example, field-for-field where fixed
spec_ev = ta.make_event("2026-09-28-16", 2.0, 1.92, -0.08, 14, 6,
                        [{"gate": "deep-veto", "B_g": 8, "P_g": 4,
                          "r_g": 0.5}], "sha256:" + "b" * 64)
check("the spec's example values reproduce (2.0 -> 1.92, B 14, P 6, "
      "deep-veto 8/4/0.5)",
      spec_ev["before_gf"] == 2.0 and spec_ev["after_gf"] == 1.92
      and spec_ev["per_gate"][0] == {"gate": "deep-veto", "B_g": 8,
                                     "P_g": 4, "r_g": 0.5}, spec_ev)

# append-only stream + alert + bridge
_sbx = Path(tempfile.mkdtemp())
tp = _sbx / "telemetry.jsonl"
ta.emit(ev, tp)
ev2 = dict(ev, rotation_id="2026-09-28-20-00")
ta.emit(ev2, tp)
events = ta.read_events(tp)
check("the stream appends in order and replays both events",
      len(events) == 2 and events[0]["rotation_id"] == "2026-09-28-16-20"
      and events[1]["rotation_id"] == "2026-09-28-20-00",
      [e["rotation_id"] for e in events])
with tp.open("rb") as f:
    raw = f.read()
check("the file is line-delimited json with no rewrite marker",
      raw.count(b"\n") == 2 and raw.endswith(b"\n"), raw[-40:])

alert = ta.make_alert("2026-09-28-16", 14, 6, -0.08)
check("the alert payload is the spec's four fields",
      alert == {"alert_type": "rotation_silent_auto_adjust",
                "rotation_id": "2026-09-28-16",
                "summary": "14 buried, 6 payers, proposed delta -0.08",
                "evidence_url": "artifacts/auto_adjust/rotation_2026-09-28-16",
                "severity": "info"}, alert)

# the bridge: a real auto_adjust record -> event + alert
rec = {"status": "applied", "before": 2.0, "after": 1.92, "delta": -0.08,
       "reason": "ok", "evidence": "sha256:" + "c" * 64,
       "bundle": str(_sbx / "bundle")}
# emit_adjustment writes to the module default; the seam swap is
# SCOPED to exactly the two bridge calls inside try/finally (the
# sink-hygiene contract) — the live-stream audit below always sees
# the REAL stream, whatever the bridge legs did.
_real_tel = ta.TELEMETRY_PATH
ta.TELEMETRY_PATH = tp
try:
    brec = ta.emit_adjustment("2026-09-28-12-16", rec, 14, 6,
                              [{"gate": "tide-out", "B_g": 6, "P_g": 2}])
    _ref = ta.emit_adjustment(
        "2026-09-28-12-17",
        {"status": "refused", "before": 2.0, "after": 2.0,
         "delta": -0.08, "reason": "rotation_already_applied",
         "evidence": ""}, 14, 6, [])
finally:
    ta.TELEMETRY_PATH = _real_tel
check("the bridge emits the record as a schema-shaped event with its "
      "alert attached",
      brec["status"] == "applied" and brec["evidence_checksum"] == rec["evidence"]
      and brec["alert"]["alert_type"] == "rotation_silent_auto_adjust"
      and brec["alert"]["evidence_url"] == str(_sbx / "bundle"), brec)
check("the replay now carries four events (two direct, one bridged "
      "applied, one bridged refused)",
      len(ta.read_events(tp)) == 4, len(ta.read_events(tp)))

# ── THE CONSUMER-CONTRACT PINS (the audit, Sep 29): the schema pins
# the producer's shape; these pins make the CONSUMERS table the live
# contract — a producer that drops a field any consumer reads goes
# red here, at the wire (emit announces it) and in the drive.
import io
from contextlib import redirect_stderr

# 1. the union obligation: every field any consumer names is one the
# producer actually emits (otherwise contract_report could never pass).
_all_consumer_fields = {f for _c in ta.CONSUMERS.values() for f in _c}
check("every consumer-contract field is a field the producer emits "
      "(the union obligation)",
      _all_consumer_fields <= set(ev),
      sorted(_all_consumer_fields - set(ev)))

# 2. every emitted family satisfies the whole table: the applied
# bridge (above) and a REFUSED record (the gate's most common row).
check("the bridged applied event feeds every consumer complete",
      ta.contract_report(brec) == [], ta.contract_report(brec))
check("a REFUSED record also feeds every consumer complete (the "
      "refused row is the stream's most common shape)",
      ta.contract_report(_ref) == [], ta.contract_report(_ref))

# 3. a dropped field is a NAMED breach, per consumer, with the reason.
for _drop, _want in (("before_gf", {"restore_state", "console_zone5",
                                    "hindsight_api"}),
                     ("timestamp", {"restore_state", "console_zone5",
                                    "hindsight_api"}),
                     ("applied_by", {"console_zone5", "hindsight_api"})):
    _broken = {k: v for k, v in ev.items() if k != _drop}
    _rep = ta.contract_report(_broken)
    _who = {r.split(":")[0] for r in _rep}
    check(f"dropping '{_drop}' breaches exactly the consumers that "
          f"read it",
          _who == _want and all(_drop in r for r in _rep),
          (_who, _rep))
check("a complete event breaches nothing",
      ta.contract_report(ev) == [], ta.contract_report(ev))

# 4. THE WIRE PIN: emit() announces a breach loudly on stderr and
# STILL appends — a half-documented event beats a missing one, and
# telemetry must never stall a trade.
_err = io.StringIO()
with redirect_stderr(_err):
    ta.emit(_broken, tp)
check("emit announces the breach on stderr (the scanner/API logs "
      "carry it)",
      "TELEMETRY SCHEMA BREACH" in _err.getvalue()
      and _drop in _err.getvalue(), _err.getvalue()[:200])
check("the breached event is STILL appended (telemetry never stalls "
      "a trade)",
      len(ta.read_events(tp)) == 5, len(ta.read_events(tp)))

# THE SEAM RESTORES HERE (before the live audit below reads the real
# stream — the sink-hygiene contract, learned twice now)
ta.TELEMETRY_PATH = _real_tel

# 5. THE CROSS-CHECK: the table names what the sources actually read.
_rs_src = (BASE / "restore_state.py").read_text(encoding="utf-8")
_app_src = (BASE / "app.py").read_text(encoding="utf-8")
_schema = json.loads((BASE / "observability" / "auto_adjust_schema.json")
                     .read_text(encoding="utf-8"))
check("every restore_state contract field is really read by "
      "restore_state's source",
      all(f'.get("{f}")' in _rs_src
          for f in ta.CONSUMERS["restore_state"]),
      [f for f in ta.CONSUMERS["restore_state"]
       if f'.get("{f}")' not in _rs_src])
check("every console zone-5 contract field is really read by "
      "app.py's audit-trail render",
      all((f'.get("{f}")' in _app_src or f".get('{f}')" in _app_src)
          for f in ta.CONSUMERS["console_zone5"]),
      [f for f in ta.CONSUMERS["console_zone5"]
       if not (f'.get("{f}")' in _app_src or f".get('{f}')" in _app_src)])
check("the API's pass-through contract covers the whole schema "
      "(zone 5 promises applied_by, cohort, commit_sha, evidence "
      "ride the event)",
      set(_schema["required"]) <= set(ta.CONSUMERS["hindsight_api"]),
      sorted(set(_schema["required"]) - set(ta.CONSUMERS["hindsight_api"])))

# 6. THE LIVE-STREAM AUDIT: the real stream feeds every consumer
# complete — the audit itself, executable. (Information when this
# environment has no live stream; a REAL row that breaches is red.)
_live = ta.read_events()
if _live:
    _bad = [(i, ta.contract_report(e)) for i, e in enumerate(_live[-200:])
            if ta.contract_report(e)]
    check(f"the LIVE stream ({len(_live)} rows) feeds every consumer "
          "complete",
          not _bad, _bad[:2])
else:
    check("no live telemetry stream in this environment (the live "
          "audit is information here)", True, "information")

import shutil
shutil.rmtree(_sbx, ignore_errors=True)

fails = [r for r in results if not r[1]]
rep = BASE / "artifacts" / "observability" / "_telemetry_drive.txt"
rep.parent.mkdir(parents=True, exist_ok=True)
rep.write_text("\n".join(f"{'PASS' if ok else 'FAIL'}  {n}" +
                         (f"  [{d}]" if d and not ok else "")
                         for n, ok, d in results)
               + f"\n\nRESULT: {len(results) - len(fails)} passed, "
                 f"{len(fails)} failed\n", encoding="utf-8")
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")


def main():
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    sys.exit(main())
