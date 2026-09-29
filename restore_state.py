"""EMERGENCY RESTORE — the tested replay path when a shared state file
is lost or torn (the ops ask: a tested restore path that replays
evidence and rehydrates state WITHOUT double-applying adjustments).

THE SOURCE OF TRUTH: the auto-adjust telemetry jsonl
(telemetry_adjust.TELEMETRY_PATH, artifacts/auto_adjust/telemetry.jsonl)
— every apply already emits a structured event (before_gf, after_gf,
status) through emit_adjustment. The guard state
(profit_guard_state.json) is rehydrated by REPLAYING applied events:

  1. factor := the newest applied event's before_gf (the pre-apply
     anchor — the ledger's own restore-target convention; 1.0 when the
     machine never applied),
  2. auto_adjust_ledger := one row per applied event (delta, before,
     after, reason, evidence checksum, ts) — the replay NEVER
     re-applies: no apply_adjustment call, no factor multiplication,
     the ledger rows land as data, not as actions.

The write goes through profit_guard._save — merge-save + the atomic
retry contract — so a restore is never the writer that tears.

Read-only on every source; the built plan is returned and only lands
when the caller passes commit=True. The drive proves byte-exact
rehydration from a synthetic journal.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import profit_guard as pg

RESTORE_NOTE = "RESTORED from telemetry replay"

DEFAULT_FACTORS = (1.0, 2.0)


def _applied_events(telemetry_path: Path) -> list[dict]:
    """Every applied auto_adjust event, oldest first. A torn line dies
    silently — the journal is append-only jsonl, so the tail is always
    whole lines by construction (the loader skips the one bad write)."""
    out = []
    try:
        for line in telemetry_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if (ev.get("event") == "auto_adjust"
                    and ev.get("status") == "applied"):
                out.append(ev)
    except Exception:
        return []
    return out


def plan_restore(telemetry_path: Path,
                 current: dict | None = None) -> dict | None:
    """The restore PLAN (data only — nothing is written): the factor
    anchor, the ledger rows, and a ts. Returns None when the journal
    carries no applied events AND there is no current state to keep
    (nothing to restore from — the caller ships the skip line)."""
    evs = _applied_events(telemetry_path)
    if not evs:
        if current:
            return {"factor": None, "ledger": [], "ts": None,
                    "kept": True}
        return None
    newest = evs[-1]
    anchor = float(newest.get("before_gf") or 0.0)
    if anchor <= 0:
        anchor = DEFAULT_FACTORS[0]
    ledger = [{"rotation_id": str(e.get("rotation_id") or ""),
               "status": "restored",
               "day": str(e.get("timestamp") or "")[:10],
               "delta": float(e.get("delta") or 0.0),
               "before": float(e.get("before_gf") or 0.0),
               "after": float(e.get("after_gf") or 0.0),
               "reason": RESTORE_NOTE,
               "evidence": str(e.get("evidence_checksum") or ""),
               "ts": str(e.get("timestamp") or "")}
              for e in evs]
    return {"factor": anchor, "ledger": ledger,
            "ts": newest.get("timestamp"), "kept": False}


def build_restore_state(plan: dict) -> dict:
    """The state dict the plan implies — the rehydrated keys only; the
    merge-save keeps every other writer's keys intact."""
    return {"factor": plan["factor"],
            "auto_adjust_ledger": plan["ledger"]}


def restore(telemetry_path: Path, commit: bool = False,
            note=None) -> dict:
    """Rehydrate the guard state from the telemetry replay. Reads
    everything first, plans, and only then (commit=True) writes once
    through profit_guard._save (merge-save + atomic retry). Returns
    {status, factor, rows, note} — status rehydrated | kept | skipped."""
    try:
        cur = pg._read_state(pg._state_file())
    except Exception:
        cur = {}
    plan = plan_restore(telemetry_path, current=cur or None)
    if plan is None:
        return {"status": "skipped", "factor": None, "rows": 0,
                "note": "no applied events in the journal"}
    if not commit:
        return {"status": "planned", "factor": plan["factor"],
                "rows": len(plan["ledger"]),
                "note": "dry run — pass commit=True to land it"}
    if plan.get("kept"):
        return {"status": "kept", "factor": None, "rows": 0,
                "note": "journal empty; current state preserved"}
    st = build_restore_state(plan)
    pg._save(st)
    if note:
        try:
            note({"op": "restore_guard_state", "target":
                  str(pg._state_file()),
                  "factor": plan["factor"], "rows": len(plan["ledger"]),
                  "ts": plan["ts"],
                  "ts_run": datetime.now().astimezone().isoformat()})
        except Exception:
            pass
    return {"status": "rehydrated", "factor": plan["factor"],
            "rows": len(plan["ledger"]),
            "note": f"anchor {plan['factor']} from {plan['ts']}"}
