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
# emit_adjustment writes to the module default; pin the path instead
ta.TELEMETRY_PATH = tp
brec = ta.emit_adjustment("2026-09-28-12-16", rec, 14, 6,
                          [{"gate": "tide-out", "B_g": 6, "P_g": 2}])
check("the bridge emits the record as a schema-shaped event with its "
      "alert attached",
      brec["status"] == "applied" and brec["evidence_checksum"] == rec["evidence"]
      and brec["alert"]["alert_type"] == "rotation_silent_auto_adjust"
      and brec["alert"]["evidence_url"] == str(_sbx / "bundle"), brec)
check("the replay now carries three events (two direct, one bridged)",
      len(ta.read_events(tp)) == 3, len(ta.read_events(tp)))

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
sys.exit(1 if fails else 0)
