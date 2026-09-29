"""THE PROPORTIONAL SELF-ADJUST (task 2, Sep 28 spec): when a silent
rotation buried payers, the guard factor moves PROPORTIONALLY to the
measured cost — and only then.

The rule (the spec's defaults, verbatim):
    delta = min(DELTA_MAX, ALPHA x r x ln(1+B))   r = P/B, B >= B_MIN,
                                                  r >= R_MIN
    negative delta LOOSENS the guard (the gates were too tight — the
    machine docked itself; loosening is the refund).

Safety rails, every one enforced in code:
    per-adjustment cap      DELTA_MAX
    daily cumulative cap    DAILY_CAP (|deltas| summed over the day)
    sample floor            B_MIN measurable burials, else refuse
    ratio floor             R_MIN, else refuse (the gates were RIGHT)
    concentration gate      one gate carrying > CONCENTRATION_MAX of the
                            payers blocks AUTO-apply (a single-gate
                            story is a story about that gate, not the
                            guard) — it queues for review instead
    time box                ADJUST_DURATION_ROTATIONS, then auto-revert
    immutable evidence      every apply carries an evidence-bundle
                            checksum; the ledger row is append-only

The factor seam is profit_guard's own state (GUARD_CLAMP bounds, the
same factor the scanner reads every cycle) — no second guard, no
parallel truth.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ── the spec's parameter block (tunable ONLY through review — see
#    governance/adjustment_policy.md) ─────────────────────────────────
ALPHA = 0.05
DELTA_MAX = 0.10
DAILY_CAP = 0.20
B_MIN = 5
R_MIN = 0.25
ADJUST_DURATION_ROTATIONS = 3
CONCENTRATION_MAX = 0.5

MIN_GF, MAX_GF = 0.5, 2.0   # profit_guard.GUARD_CLAMP, restated for the clamp

# THE FILL-HONESTY COUPLING (Sep 28 late ask: peaks overstate realized
# P&L). The console panel prices both (app.SPREAD/FEE_RT_PCT) and this
# module scales ALPHA by the modeled capture ratio, floored — a rotation
# whose payers were chart highs the book could never have filled learns
# proportionally LESS.
FILL_CAPTURE_FLOOR = 0.10

def scale_alpha_for_fills(alpha: float, capture_ratio: float) -> float:
    """The fill-adjusted alpha: peaks that are 60% real pay 60% of the
    nudge. The floor keeps a terrible-model rotation from widening the
    guard to zero — conservative direction, always."""
    c = max(FILL_CAPTURE_FLOOR, min(1.0, float(capture_ratio or 0.0)))
    return alpha * c

EVIDENCE_DIR = ROOT / "artifacts" / "auto_adjust"


# ── the rule, pure ──────────────────────────────────────────────────
def compute_delta(B: int, P: int, alpha: float | None = None,
                  delta_max: float | None = None) -> tuple[float, str]:
    """The conservative delta for B measurable burials / P payers.
    Returns (delta, reason); delta is 0.0 with a named reason whenever
    the rule refuses. alpha/delta_max (Sep 29, the review's major):
    the CANARY'S SELECTED CELL overrides the module defaults, so the
    boundary hook evaluates the parameters the gate actually armed —
    a None keeps each module default (every legacy caller)."""
    if B < B_MIN:
        return 0.0, "insufficient_samples"
    r = P / B if B > 0 else 0.0
    if r < R_MIN:
        return 0.0, "low_ratio"
    _a = ALPHA if alpha is None else float(alpha)
    _dm = DELTA_MAX if delta_max is None else float(delta_max)
    delta = min(_dm, _a * r * math.log(1 + B))
    # negative delta loosens guard
    return -delta, "ok"


def clamp(before: float, delta: float) -> float:
    """The proposed after, inside the machine's guard clamp."""
    return round(max(MIN_GF, min(MAX_GF, before + delta)), 4)


def concentration(per_gate: list[dict]) -> float:
    """The biggest gate's share of payers (0.0 when no payers)."""
    P = sum(int(g.get("P_g") or 0) for g in per_gate)
    if P <= 0:
        return 0.0
    return max(int(g.get("P_g") or 0) for g in per_gate) / P


# ── the state seams (profit_guard's own state file, sandboxable) ────
def _pg():
    import profit_guard as pg
    return pg


def read_guard_factor() -> float:
    pg = _pg()
    return float(pg.stored_factor() or 1.0)


def _ledger(st: dict) -> list:
    return st.setdefault("auto_adjust_ledger", [])


def _today_key(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def daily_delta_sum(st: dict, now: datetime) -> float:
    """|delta| total applied TODAY — the daily cap's numerator."""
    key = _today_key(now)
    return sum(abs(float(r.get("delta") or 0)) for r in _ledger(st)
               if str(r.get("day")) == key
               and r.get("status") in ("applied", "reverted"))


# ── the evidence bundle (immutable, checksummed) ────────────────────
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
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def write_evidence_bundle(rotation_id: str, per_gate: list[dict],
                          B: int, P: int, r: float | None) -> tuple[str, Path]:
    """Bundle = per-gate ratios csv + the rotation's peak table +
    manifest with checksums and the commit sha. Returns (checksum, dir).
    Written ONCE — an existing bundle is never rewritten (immutability)."""
    import per_gate_export as pge
    d = EVIDENCE_DIR / f"rotation_{rotation_id}"
    if d.exists():
        man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        return man["bundle_checksum"], d
    d.mkdir(parents=True, exist_ok=True)
    pge.export_csv(per_gate, d / "per_gate_ratios.csv")
    (d / "summary.json").write_text(json.dumps(
        {"rotation_id": rotation_id, "B": B, "P": P, "r": r,
         "per_gate": per_gate}, indent=2), encoding="utf-8")
    files = sorted(p.name for p in d.iterdir() if p.is_file())
    manifest = {
        "rotation_id": rotation_id,
        "commit_sha": _commit_sha(),
        "created_at": datetime.now().astimezone().isoformat(),
        "files": {name: _sha256_file(d / name) for name in files},
    }
    blob = json.dumps(manifest, sort_keys=True).encode("utf-8")
    manifest["bundle_checksum"] = \
        "sha256:" + hashlib.sha256(blob).hexdigest()
    (d / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                     encoding="utf-8")
    return manifest["bundle_checksum"], d


# ── apply / revert ──────────────────────────────────────────────────
def set_guard_factor_temporarily(after: float,
                                 rotations: int = ADJUST_DURATION_ROTATIONS,
                                 before: float | None = None) -> None:
    """Write the time-boxed factor into profit_guard's state. The box
    rides the state (rotations_left), ticked down per rotation by
    tick_rotation(); expire_if_due() restores `before` when it empties."""
    pg = _pg()
    st = pg._state()
    st["factor"] = float(after)
    _prior = st.get("auto_adjust_box")
    if isinstance(_prior, dict) and _prior.get("before") is not None \
            and before is None:
        # THE STACKED BOX (Sep 29 review major): a second adjustment on
        # a live box keeps the ORIGINAL before — the box's before is
        # the restore target, and rewriting it to the already-loosened
        # factor would strand the first loosening forever.
        _before = float(_prior["before"])
    else:
        _before = float(pg.stored_factor() if before is None else before)
    st["auto_adjust_box"] = {
        "after": float(after),
        "before": _before,
        "rotations_left": int(rotations),
        "set_at": datetime.now().astimezone().isoformat(),
    }
    # OWNER-KEY SAVE (the round-2 review's degeneration major): the
    # merge-save overlays onto the file's CURRENT contents — a full
    # stale snapshot passed here would overwrite every other writer's
    # key with old values. Only this module's keys ride.
    pg._save({"factor": st["factor"],
              "auto_adjust_box": st["auto_adjust_box"]})


def tick_rotation() -> int:
    """One rotation elapsed: decrement the box. Returns rotations left."""
    pg = _pg()
    st = pg._state()
    box = st.get("auto_adjust_box")
    if not box:
        return 0
    box["rotations_left"] = int(box.get("rotations_left") or 0) - 1
    pg._save({"auto_adjust_box": box})   # owner-key only (round-2)
    return int(box["rotations_left"])


def expire_if_due() -> dict | None:
    """Restore the pre-adjust factor when the box ran out. The
    auto-revert: the loosening was temporary by construction."""
    pg = _pg()
    st = pg._state()
    box = st.get("auto_adjust_box")
    if not box:
        return None
    if int(box.get("rotations_left") or 0) > 0:
        return {"rotations_left": int(box["rotations_left"])}
    before = float(box.get("before") or 1.0)
    st["factor"] = before
    st.pop("auto_adjust_box", None)
    st["auto_adjust_box"] = None   # the merge-save's None-means-delete:
                                    # the box must be REMOVED from the
                                    # file, not resurrected from it
    _ledger(st).append({
        "day": _today_key(datetime.now().astimezone()),
        "rotation_id": str(box.get("rotation_id") or ""),
        "status": "reverted", "before": float(box.get("after") or 0),
        "after": before, "delta": 0.0,
        "reason": "time_box_expired",
        "evidence": str(box.get("evidence") or ""),
        "applied_at": datetime.now().astimezone().isoformat(),
    })
    # OWNER-KEY SAVE (round-2): factor + ledger + the tombstone — the
    # None-means-delete contract removes the box from the FILE without
    # a stale full snapshot riding along.
    pg._save({"factor": st["factor"],
              "auto_adjust_ledger": st["auto_adjust_ledger"],
              "auto_adjust_box": None})
    return {"reverted_to": before}


def apply_adjustment(delta: float, rotation_id: str,
                     evidence_checksum: str,
                     per_gate: list[dict] | None = None,
                     auto: bool = True,
                     override_concentration: bool = False,
                     params: dict | None = None) -> dict:
    """Enforce every rail, then move the factor. Never raises: the
    returned dict carries status applied / queued / refused + reason.
    override_concentration=True lifts ONLY the concentration queue
    (the operator's named review) — the caps and floors still bite.
    params (Sep 29, THE CANARY PARAMS SEAM): the armed cell's
    alpha/delta_max/daily_cap override the module defaults FOR THIS
    CALL — ops/canary_enable arms the gate-selected cell and the
    boundary hook threads it through; every record carries the cell
    it evaluated under (the armed params are provably live)."""
    pg = _pg()
    now = datetime.now().astimezone()
    st = pg._state()
    before = read_guard_factor()
    # THE EFFECTIVE CELL (the params seam): each key falls back to the
    # module default when the armed cell omits it — a partial cell is
    # honored key-by-key, never all-or-nothing.
    _p = params or {}
    _cap = DAILY_CAP if _p.get("daily_cap") is None else float(_p["daily_cap"])
    rec = {"day": _today_key(now), "rotation_id": rotation_id,
           "status": "refused", "before": before, "after": before,
           "delta": float(delta), "reason": "",
           "evidence": evidence_checksum,
           "params": {"alpha": (ALPHA if _p.get("alpha") is None
                                else float(_p["alpha"])),
                      "delta_max": (DELTA_MAX if _p.get("delta_max") is None
                                    else float(_p["delta_max"])),
                      "daily_cap": _cap},
           "applied_at": now.isoformat()}
    try:
        if delta >= 0:
            rec["reason"] = "non_negative_delta_is_not_self_adjust"
        elif any(r.get("status") == "applied"
                 and str(r.get("rotation_id") or "") == rotation_id
                 for r in _ledger(st)):
            # THE DOUBLE-APPLY GUARD (Sep 29 review major): an `applied`
            # row for the SAME rotation refuses a second apply — the
            # boundary hook self-heals per tick and a cadence/restart
            # retry must never double-loosen the guard. Evaluated after
            # the concentration gate (that ordering is the earlier
            # review's own fix) and before the daily cap: the dedupe is
            # about THIS rotation, not the day's budget.
            rec["status"] = "refused"
            rec["reason"] = "rotation_already_applied"
            _ledger(st).append(rec)
            pg._save({"auto_adjust_ledger": _ledger(st)})
            return rec
        elif (per_gate and not override_concentration
              and concentration(per_gate) > CONCENTRATION_MAX):
            # THE CONCENTRATION GATE EVALUATES FIRST (the review's
            # ordering minor): the operator is offered the review while
            # the day still has cap budget — by the time the cap has
            # burned, queueing is moot. The cap check follows unchanged.
            # THE QUEUE DECISION RIDES ONLY THE CONCENTRATION AND THE
            # OVERRIDE (the isolated review's major, restated as a
            # rule): never which caller asked. `auto` stays in the
            # signature for compatibility but cannot lift the gate —
            # only override_concentration, the authenticated operator,
            # can. auto=False is a REQUEST for review, not a bypass.
            rec["status"] = "queued"
            rec["reason"] = "concentration_gate_blocks_auto_apply"
            _ledger(st).append(rec)
            # OWNER-KEY SAVE (the round-2 review's degeneration major):
            # a full stale `st` merged over the file degenerates the
            # merge-save into a whole-file rewrite — every key it
            # carries overwrites the merge with an old value. Each
            # write carries ONLY its owner keys; the merge keeps the
            # other writer's (self_adjust's) live state.
            pg._save({"auto_adjust_ledger": _ledger(st)})
            return rec
        elif daily_delta_sum(st, now) + abs(delta) > _cap + 1e-9:
            rec["reason"] = "daily_cap_exceeded"
        else:
            after = clamp(before, delta)
            rec.update({"status": "applied", "after": after,
                        "reason": "ok",
                        "expires_after_rotations":
                            ADJUST_DURATION_ROTATIONS})
            _ledger(st).append(rec)
            # OWNER-KEY SAVES (round-2): land the ledger FIRST — the
            # setter re-reads, two writers never clobber — then the
            # setter writes its own fresh view, then only the box rides
            # the last save. Never a full stale snapshot.
            pg._save({"auto_adjust_ledger": _ledger(st)})
            set_guard_factor_temporarily(after, ADJUST_DURATION_ROTATIONS,
                                         before=before)
            box = pg._state().get("auto_adjust_box") or {}   # re-read:
            box.update({"rotation_id": rotation_id,          # the setter
                        "evidence": evidence_checksum})      # wrote its own
            pg._save({"auto_adjust_box": box})
            return rec
        _ledger(st).append(rec)
        pg._save({"auto_adjust_ledger": _ledger(st)})   # owner-key only
        return rec
    except Exception as exc:                       # never break the caller
        rec["reason"] = f"apply_error: {exc!r}"
        try:
            _ledger(st).append(rec)
            pg._save({"auto_adjust_ledger": _ledger(st)})
        except Exception:
            pass
        return rec


def rollback(rotation_id: str) -> dict:
    """The operator's undo: restore the last applied adjustment's
    `before` and mark the row reverted."""
    pg = _pg()
    now = datetime.now().astimezone()
    st = pg._state()
    rows = [r for r in _ledger(st)
            if r.get("rotation_id") == rotation_id
            and r.get("status") == "applied"]
    if not rows:
        return {"status": "refused", "reason": "no_applied_adjustment"}
    row = rows[-1]
    before = float(row["before"])
    st["factor"] = before
    st.pop("auto_adjust_box", None)
    st["auto_adjust_box"] = None   # None-means-delete under merge-save
    _ledger(st).append({
        "day": _today_key(now), "rotation_id": rotation_id,
        "status": "reverted", "before": float(row["after"]),
        "after": before, "delta": 0.0, "reason": "operator_rollback",
        "evidence": str(row.get("evidence") or ""),
        "applied_at": now.isoformat(),
    })
    # OWNER-KEY SAVE (round-2): factor + ledger + the box tombstone.
    pg._save({"factor": st["factor"],
              "auto_adjust_ledger": st["auto_adjust_ledger"],
              "auto_adjust_box": None})
    return {"status": "reverted", "restored_to": before}


def propose_from_aggregate(rotation_id: str, B: int, P: int,
                           per_gate: list[dict],
                           operator_override: bool = False,
                           params: dict | None = None) -> dict:
    """The full path for one rotation: evidence bundle, delta, verdict.
    Applies ONLY when every rail passes; else queues/refuses with the
    bundle still written (the evidence outlives the decision).
    operator_override=True is the operator's queue_review decision
    arriving with a name on it: the concentration gate's queue is
    lifted (the review HAPPENED) — every other rail still bites."""
    delta, reason = compute_delta(
        B, P, alpha=(params or {}).get("alpha"),
        delta_max=(params or {}).get("delta_max"))
    cks, d = write_evidence_bundle(
        rotation_id, per_gate, B, P, (P / B) if B else None)
    if reason != "ok":
        return {"status": "refused", "reason": reason, "delta": 0.0,
                "evidence": cks, "bundle": str(d)}
    conc = concentration(per_gate)
    # THE CELL RIDES THE WHOLE PATH (the params seam, completed): the
    # same params the delta was computed under govern the caps inside
    # apply_adjustment — and land in its record for the proof.
    rec = apply_adjustment(
        delta, rotation_id, cks, per_gate=per_gate,
        auto=conc <= CONCENTRATION_MAX,
        override_concentration=operator_override, params=params)
    rec["bundle"] = str(d)
    rec["concentration"] = round(conc, 4)
    return rec
