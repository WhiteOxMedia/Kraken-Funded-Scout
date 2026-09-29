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
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TELEMETRY_PATH = ROOT / "artifacts" / "auto_adjust" / "telemetry.jsonl"


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
    the path so a caller can hand it to the alert payload."""
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
