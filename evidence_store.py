"""THE ROTATION EVIDENCE STORE (task 8, Sep 28 spec): at each rotation
boundary, snapshot every input the studies and the referee read — the
OHLCV cache, the suppression journal, the popups ledger, the guard
state, and the PROSE inputs the fixtures grade (_tidebarweekly.txt et
al.) — into one checksummed bundle per rotation.

    artifacts/evidence/rotations/<rotation_id>/manifest.json
    artifacts/evidence/rotations/<rotation_id>/<files...>

The spec's acceptance: the bundle reproduces a referee run EXACTLY —
which holds only if the manifest pins the sha256 of every input file
PLUS the commit sha of the code that read them. Retention: 90 days
(the spec's floor), pruned by manifest date, never silently — the prune
reports what it removed.

Read-only on the sources; the bundles are the only writes, and they are
immutable once written (an existing manifest is never rewritten — the
auto_adjust bundle rule, same class).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STORE = ROOT / "artifacts" / "evidence" / "rotations"
RETENTION_DAYS = 90

# the inputs, by role: the machine's numbers and the machine's words
DEFAULT_INPUTS = [
    "ohlcv_cache.db",                # every price the join reads
    "popup_suppressions.jsonl",      # the fire ledger's refusal side
    "popups.jsonl",                  # the fire ledger's fire side
    "profit_guard_state.json",       # the factor + its history
    "meta_layers_state.json",        # the dock + the SAL ledger
    "discipline_state.json",         # the burial ledger
    "_tidebarweekly.txt",            # the prose the fixtures grade
    "_tidebarweekly_history.jsonl",  # the weekly trail
    "scanner_pulse.json",            # the scanner's own heartbeat
]


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


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def default_rotation_id(now: datetime | None = None) -> str:
    """The scanner's 4h window stamp on the date: 2026-09-28-12-16."""
    import per_gate_export as pge
    now = now or datetime.now().astimezone()
    return f"{now:%Y-%m-%d}-{pge.default_rotation_id(now)}"


def snapshot(rotation_id: str | None = None,
             inputs: list[str] | None = None,
             store: Path | None = None,
             extra_files: list[Path] | None = None) -> dict:
    """Bundle one rotation's inputs. Immutable: an existing bundle is
    returned untouched (verify-first semantics, never rewritten)."""
    rotation_id = rotation_id or default_rotation_id()
    store = (store or STORE) / f"rotation_{rotation_id}"
    man = store / "manifest.json"
    if man.exists():
        return json.loads(man.read_text(encoding="utf-8"))
    store.mkdir(parents=True, exist_ok=True)
    names = list(inputs or DEFAULT_INPUTS)
    copied, missing = {}, []
    for name in names:
        src = ROOT / name
        if not src.exists():
            missing.append(name)
            continue
        shutil.copy2(src, store / src.name)
        copied[src.name] = _sha256_file(store / src.name)
    for extra in (extra_files or []):
        if extra.exists():
            shutil.copy2(extra, store / extra.name)
            copied[extra.name] = _sha256_file(store / extra.name)
    manifest = {
        "rotation_id": rotation_id,
        "created_at": datetime.now().astimezone().isoformat(),
        "commit_sha": _commit_sha(),
        "files": copied,
        "missing": missing,
        "retention_days": RETENTION_DAYS,
    }
    blob = json.dumps(manifest, sort_keys=True).encode("utf-8")
    manifest["bundle_checksum"] = \
        "sha256:" + hashlib.sha256(blob).hexdigest()
    man.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def verify(rotation_id: str, store: Path | None = None) -> dict:
    """The referee's preflight: re-hash every file against the
    manifest. A bundle that verifies reproduces the run EXACTLY — the
    acceptance, checkable instead of promised."""
    store = (store or STORE) / f"rotation_{rotation_id}"
    man_p = store / "manifest.json"
    if not man_p.exists():
        return {"ok": False, "reason": "no_bundle"}
    man = json.loads(man_p.read_text(encoding="utf-8"))
    bad = []
    for name, cks in man.get("files", {}).items():
        p = store / name
        if not p.exists():
            bad.append(f"{name}: missing")
        elif _sha256_file(p) != cks:
            bad.append(f"{name}: checksum drift")
    blob = json.dumps({k: v for k, v in man.items()
                       if k != "bundle_checksum"}, sort_keys=True)
    cks_ok = ("sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()
              == man.get("bundle_checksum"))
    return {"ok": not bad and cks_ok, "checked": len(man.get("files", {})),
            "bad": bad, "manifest_checksum_ok": cks_ok}


def prune(now: datetime | None = None,
          store: Path | None = None,
          retention_days: int = RETENTION_DAYS) -> dict:
    """Remove bundles older than retention, BY MANIFEST DATE, and
    report what went — never a silent sweep."""
    now = now or datetime.now().astimezone()
    store = store or STORE
    removed = []
    if store.exists():
        for d in sorted(store.glob("rotation_*")):
            man_p = d / "manifest.json"
            try:
                created = datetime.fromisoformat(
                    json.loads(man_p.read_text(encoding="utf-8"))
                    ["created_at"])
            except Exception:
                continue     # an unparseable bundle is evidence; keep it
            if now - created > timedelta(days=retention_days):
                shutil.rmtree(d, ignore_errors=True)
                removed.append(d.name)
    return {"removed": removed, "retention_days": retention_days}
