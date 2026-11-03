"""THE TELEMETRY EMITTER (task 4, Sep 28 spec): every auto-adjust
decision becomes ONE spec-shaped event on an append-only jsonl, and a
breach becomes a tripwire alert payload — both consumed by the wireframe
/API layer (task 3) and the canary runner (task 6).

The stream lives at artifacts/auto_adjust/telemetry.jsonl (append-only
by construction: every write opens in 'a' mode, nothing ever rewrites).
The evidence bundle checksum, commit sha, and cohort ride every event so
an auditor can walk event -> bundle -> commit with no second source.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TELEMETRY_PATH = ROOT / "artifacts" / "auto_adjust" / "telemetry.jsonl"

# ── THE CONSUMER CONTRACT (the audit's table, Sep 29): every field a
# consumer READS from an event, keyed by consumer with the reason it
# is needed. The producers' obligation is not just the schema — it is
# every downstream read. contract_report() turns a dropped field into
# a NAMED breach; emit() announces one loudly instead of appending a
# half-event nobody can consume. Adding a consumer read means adding
# its field here — the drives cross-check the table against the
# consumers' sources, so a new read with no contract row goes red.
CONSUMERS = {
    "restore_state": {
        "event": "the replay filter selects only event=auto_adjust",
        "status": "only applied rows rehydrate the guard factor",
        "before_gf": "the factor ANCHOR — a missing one silently "
                     "restores 1.0 and resizes every gate",
        "after_gf": "the restored ledger row's after value",
        "delta": "the restored ledger row's delta",
        "timestamp": "the ledger row's day and the plan's ts",
        "evidence_checksum": "the restored row's evidence chain",
        "rotation_id": "the restored row's identity",
    },
    "console_zone5": {
        "timestamp": "the audit row's stamp (rendered to the minute)",
        "rotation_id": "the audit row's identity",
        "status": "APPLIED/REFUSED/QUEUED badge",
        "before_gf": "the before → after factor render",
        "after_gf": "the before → after factor render",
        "B": "the buried count",
        "P": "the payer count",
        "r": "the payer rate (None renders as —)",
        "applied_by": "who/what applied it (system/operator/rotation_boundary)",
        "commit_sha": "the event → commit walk",
        "evidence_checksum": "the event → evidence-bundle walk",
        "reason": "the non-ok reason line",
    },
    "hindsight_api": {
        # the API is a pass-through (zone-5 wire: read_events → /telemetry
        # unchanged) — its contract is the SCHEMA: every required field
        # rides the wire because the docstring promises them.
        "event": "schema required",
        "rotation_id": "schema required",
        "before_gf": "schema required",
        "after_gf": "schema required",
        "delta": "schema required",
        "B": "schema required",
        "P": "schema required",
        "r": "schema required",
        "per_gate": "schema required",
        "evidence_checksum": "schema required + the docstring's promise",
        "commit_sha": "schema required + the docstring's promise",
        "cohort": "schema required + the docstring's promise",
        "applied_by": "schema required + the docstring's promise",
        "status": "schema required",
        "reason": "schema required",
        "timestamp": "schema required",
    },
}


def contract_report(event: dict) -> list[str]:
    """Every consumer-contract breach in one event, named. [] means
    the event feeds every consumer complete."""
    breaches = []
    if not isinstance(event, dict):
        return ["event is not a dict — no consumer can read it"]
    for _consumer, _fields in CONSUMERS.items():
        for _field, _why in _fields.items():
            if _field not in event:
                breaches.append(f"{_consumer}: missing '{_field}' "
                                f"({_why})")
    return breaches


def _commit_sha() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"],
                             capture_output=True, text=True, timeout=10,
                             cwd=str(ROOT))
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def make_event(rotation_id: str, before: float, after: float,
               delta: float, B: int, P: int, per_gate: list[dict],
               evidence_checksum: str, cohort: str = "canary-1",
               applied_by: str = "system", status: str = "applied",
               reason: str = "ok", now: datetime | None = None) -> dict:
    """The spec's event shape, field for field."""
    now = now or datetime.now().astimezone()
    return {
        "event": "auto_adjust",
        "rotation_id": rotation_id,
        "before_gf": float(before),
        "after_gf": float(after),
        "delta": float(delta),
        "B": int(B),
        "P": int(P),
        "r": (P / B) if B else None,
        "per_gate": [{"gate": g.get("gate"),
                      "B_g": int(g.get("B_g",
                                       g.get("measurable_B_g") or 0)),
                      "P_g": int(g.get("P_g") or 0),
                      "r_g": g.get("r_g")}
                     for g in (per_gate or [])],
        "evidence_checksum": evidence_checksum,
        "commit_sha": _commit_sha(),
        "cohort": cohort,
        "applied_by": applied_by,
        "status": status,
        "reason": reason,
        "timestamp": now.isoformat(),
    }


def emit(event: dict, path: Path | None = None) -> Path:
    """Append one event; the file is created on first write. Returns
    the path so a caller can hand it to the alert payload. THE SCHEMA
    PIN AT THE WIRE: a dropped consumer field is announced loudly on
    stderr (the scanner/API stderr logs carry it) — but the append
    still happens, because a half-documented event beats a missing one
    and telemetry must never stall a trade."""
    _breaches = contract_report(event)
    if _breaches:
        print(f"⚠️ TELEMETRY SCHEMA BREACH ({len(_breaches)} consumer "
              f"fields missing): {'; '.join(_breaches)}",
              file=sys.stderr)
    p = path or TELEMETRY_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    return p


def make_alert(rotation_id: str, B: int, P: int, delta: float,
               evidence_dir: Path | None = None,
               severity: str = "info") -> dict:
    """The tripwire payload: a silent auto-adjust is exactly the kind
    of thing the operator must see even when nothing is wrong."""
    summary = (f"{B} buried, {P} payers, proposed delta {delta:+.2f}")
    return {
        "alert_type": "rotation_silent_auto_adjust",
        "rotation_id": rotation_id,
        "summary": summary,
        "evidence_url": (f"artifacts/auto_adjust/rotation_{rotation_id}"
                         if evidence_dir is None else str(evidence_dir)),
        "severity": severity,
    }


def emit_adjustment(rotation_id: str, rec: dict, B: int, P: int,
                    per_gate: list[dict], cohort: str = "canary-1",
                    applied_by: str = "system") -> dict:
    """Bridge: an auto_adjust.apply_adjustment result record becomes a
    schema-shaped event + an alert, both emitted. Returns the event."""
    ev = make_event(rotation_id,
                    before=rec.get("before", 0.0),
                    after=rec.get("after", 0.0),
                    delta=rec.get("delta", 0.0),
                    B=B, P=P, per_gate=per_gate,
                    evidence_checksum=str(rec.get("evidence") or ""),
                    cohort=cohort, applied_by=applied_by,
                    status=rec.get("status", "refused"),
                    reason=rec.get("reason", ""))
    emit(ev)
    ev["alert"] = make_alert(rotation_id, B, P, rec.get("delta", 0.0),
                             evidence_dir=rec.get("bundle"))
    return ev


def read_events(path: Path | None = None) -> list[dict]:
    """The audit trail, replayed — the dashboard/API's read side."""
    p = path or TELEMETRY_PATH
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
