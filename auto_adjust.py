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
                                 before: float | None = None) -> dict:
    """Write the time-boxed factor into profit_guard's state. The box
    rides the state (rotations_left), ticked down per rotation by
    tick_rotation(); expire_if_due() restores `before` when it empties.
    THE WRITE-TIME TRUTH (the gate's non-atomic-revalidation [high] on
    the re-arm decision): the countdown is the box's OWN state as it
    stands at the WRITE — a stack carries the box's remaining rotations
    read HERE, never the caller's snapshot, so a heartbeat tick landing
    inside the write's window is never written back over; the caller's
    `rotations` arms only a BIRTH. A box DUE at the write expires first
    (the same drift-aware expiry the heartbeat runs) and the write
    births from the POST-expiry anchor. Returns {"box": <the box
    written>, "expired_due_box": bool} so the caller can reconcile its
    record when the write had to bury a box first."""
    pg = _pg()
    st = pg._state()
    _prior = st.get("auto_adjust_box")
    _prior_rot = (int(_prior["rotations_left"])
                  if isinstance(_prior, dict)
                  and _prior.get("rotations_left") is not None else None)
    _expired_due = False
    if _prior_rot is not None and _prior_rot <= 0:
        # THE DUE BOX AT THE WRITE (the third-order window the write-time
        # rule opens): max(1, 0) here would re-arm a spent guarantee one
        # rotation at a time. The box's business runs where the write
        # lands — expire (drift-aware, ledgered), then birth from the
        # POST-expiry truth: the caller's `before` is a pre-expiry
        # snapshot and never anchors a fresh guarantee.
        expire_if_due()
        st = pg._state()
        _prior = st.get("auto_adjust_box")
        _prior_rot = (int(_prior["rotations_left"])
                      if isinstance(_prior, dict)
                      and _prior.get("rotations_left") is not None else None)
        _expired_due = True
    if _prior_rot is not None:
        # THE STACKED BOX (Sep 29 review major; Oct 2 production-shape
        # fix): a second adjustment stacking onto a LIVE box keeps the
        # ORIGINAL before — the box's before is the restore target, and
        # rewriting it to the already-adjusted factor would strand the
        # first adjustment forever. The live box's before wins WHENEVER
        # A BOX EXISTS: apply_adjustment (the only production caller)
        # always passes before=the LIVE factor, which is already the
        # box's after whenever a box is up, so the old `before is None`
        # side-condition made this branch dead code on the production
        # path — only the drive's direct-setter pin ever ran it. AND THE
        # WRITE-TIME RULE (this [high]'s cure): the carried countdown is
        # read HERE, at the write — the caller's number is a snapshot.
        _rot = max(1, _prior_rot)
        if isinstance(_prior, dict) and _prior.get("before") is not None:
            _before = float(_prior["before"])
        else:
            _before = float(pg.stored_factor() if before is None else before)
    elif _expired_due:
        # THE POST-EXPIRY BIRTH: the restored anchor (or, under drift,
        # the recompute's standing factor) is the birth's base — the
        # guarantee that died at this write is gone; the new one starts
        # clean with the caller's full window.
        _before = float(st.get("factor") or 0.0)
        _rot = int(rotations)
    else:
        _before = float(pg.stored_factor() if before is None else before)
        _rot = int(rotations)
    st["factor"] = float(after)
    st["auto_adjust_box"] = {
        "after": float(after),
        "before": _before,
        "rotations_left": _rot,
        "set_at": datetime.now().astimezone().isoformat(),
    }
    # THE SAVE-TIME GUARD (the gate's [high] on 393ba92 — the last open
    # window, R7/R8): the read-time divert above only sees a box already
    # due at the read; a heartbeat tick/expiry landing between that read
    # and this save would be overwritten by the snapshot just built —
    # auto_adjust_box is an owner key, so the merge-save would resurrect
    # the dead box and clobber the post-expiry factor truth. The guard
    # runs INSIDE the merge-save against the file's CURRENT contents —
    # the last point where the fresh truth is visible — and amends the
    # merged write in place:
    #   · the cur box is DUE (the tick landed, the heartbeat's expiry is
    #     in flight): cur's box stands — the payload's stale live box
    #     must not re-arm it; the in-flight expiry owns the ledger and
    #     the tombstone, and expired_due_box tells the caller.
    #   · the cur box is GONE (the expiry landed in the window): birth
    #     in the merge from the restored anchor — the guarantee that
    #     died here is buried by the heartbeat's own ledgered expiry.
    #   · the cur box TICKED (rot < the read's): carry the FRESH
    #     countdown and the original before — the write-time rule,
    #     enforced one last time at the write.
    _raced = {"expired": False}

    def _save_guard(merged, cur):
        _cur_box = cur.get("auto_adjust_box")
        _cur_rot = (int(_cur_box["rotations_left"])
                    if isinstance(_cur_box, dict)
                    and _cur_box.get("rotations_left") is not None else None)
        if _cur_rot is not None and _cur_rot <= 0:
            # THE EXPIRY RUNS HERE, inline (Copilot's second [high] on
            # 39079b5): keeping cur's rot-0 box under the setter's new
            # factor hands the in-flight heartbeat expiry a drift and
            # loses the anchor. The heartbeat's own three moves, landed
            # in THIS write instead of a second one — minus the nested
            # save an expire_if_due call here would make: the drift check
            # against the FILE's factor, the row ledgered at the anchor's
            # truth, the birth from the post-expiry anchor.
            _bx_after = float(_cur_box.get("after") or 0.0)
            _bf = _cur_box.get("before")
            _bf = 1.0 if _bf is None else float(_bf)
            _cur_f = float(cur.get("factor") or 0.0)
            _intact = abs(_cur_f - _bx_after) <= 1e-9
            _now = datetime.now().astimezone()
            merged.setdefault("auto_adjust_ledger", []).append({
                "day": _today_key(_now),
                "rotation_id": str(_cur_box.get("rotation_id") or ""),
                "status": "reverted" if _intact else "expired",
                "before": _bx_after,
                "after": _bf if _intact else _cur_f,
                "delta": 0.0,
                "reason": ("time_box_expired" if _intact
                           else "time_box_expired_stale_anchor"),
                "evidence": str(_cur_box.get("evidence") or ""),
                "applied_at": _now.isoformat(),
            })
            merged["auto_adjust_box"] = {
                "after": float(after),
                "before": _bf if _intact else _cur_f,
                "rotations_left": max(1, int(rotations)),
                "set_at": _now.isoformat(),
            }
            _raced["expired"] = True
            return
        if _prior_rot is not None and _cur_box is None:
            merged["auto_adjust_box"] = {
                "after": float(after),
                "before": float(cur.get("factor") or 0.0),
                "rotations_left": max(1, int(rotations)),
                "set_at": datetime.now().astimezone().isoformat(),
            }
            _raced["expired"] = True
            return
        if (_prior_rot is not None and _cur_rot is not None
                and _cur_rot < _prior_rot):
            _sb = _cur_box.get("before")
            merged["auto_adjust_box"] = {
                "after": float(after),
                "before": (float(_sb) if _sb is not None
                           else float(cur.get("factor") or 0.0)),
                "rotations_left": max(1, _cur_rot),
                "set_at": datetime.now().astimezone().isoformat(),
            }

    # OWNER-KEY SAVE (the round-2 review's degeneration major): the
    # merge-save overlays onto the file's CURRENT contents — a full
    # stale snapshot passed here would overwrite every other writer's
    # key with old values. Only this module's keys ride — and the guard
    # above corrects them one last time against the fresh file.
    pg._save({"factor": st["factor"],
              "auto_adjust_box": st["auto_adjust_box"]},
             guard=_save_guard)
    if _raced["expired"]:
        st["auto_adjust_box"] = pg._state().get("auto_adjust_box")
        _expired_due = True
    return {"box": (dict(st["auto_adjust_box"])
                    if isinstance(st.get("auto_adjust_box"), dict) else None),
            "expired_due_box": _expired_due}


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
    auto-revert: the loosening was temporary by construction. THE
    STALE-ANCHOR GUARD (Oct 3, the wiring decision): the factor's other
    writer — the nightly cooldown recompute — may have moved the factor
    since the chain last landed it, and restoring a stale anchor over a
    fresher recompute would yank the guard against newer data; a
    drifted box retires WITHOUT the restore (the recompute owns the
    factor), ledgered by name. Production wiring: the boundary
    heartbeat (background_scanner's contest_light hook) ticks once per
    closed rotation, canary-independent."""
    pg = _pg()
    st = pg._state()
    box = st.get("auto_adjust_box")
    if not box:
        return None
    if int(box.get("rotations_left") or 0) > 0:
        return {"rotations_left": int(box["rotations_left"])}
    before = box.get("before")
    before = 1.0 if before is None else float(before)   # a valid 0.0
    # peak must not read as missing (the zero-fallback class, round 6)
    _now = datetime.now().astimezone()
    _cur = float(st.get("factor") or 0.0)
    _after = float(box.get("after") or 0.0)
    st["auto_adjust_box"] = None   # the merge-save's None-means-delete:
                                    # the box must be REMOVED from the
                                    # file, not resurrected from it
    if abs(_cur - _after) > 1e-9:
        # THE DRIFTED BOX: the world moved on — the recompute's factor
        # is fresher than this chain's landing. Retire WITHOUT the
        # restore; the anchor's story already lives in the chain's rows.
        _ledger(st).append({
            "day": _today_key(_now),
            "rotation_id": str(box.get("rotation_id") or ""),
            "status": "expired", "before": _after, "after": _cur,
            "delta": 0.0, "reason": "time_box_expired_stale_anchor",
            "evidence": str(box.get("evidence") or ""),
            "applied_at": _now.isoformat(),
        })
        pg._save({"auto_adjust_ledger": st["auto_adjust_ledger"],
                  "auto_adjust_box": None})
        return {"superseded": True, "factor": _cur}
    st["factor"] = before
    _ledger(st).append({
        "day": _today_key(_now),
        "rotation_id": str(box.get("rotation_id") or ""),
        "status": "reverted", "before": _after,
        "after": before, "delta": 0.0,
        "reason": "time_box_expired",
        "evidence": str(box.get("evidence") or ""),
        "applied_at": _now.isoformat(),
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
    # THE RE-ARM DECISION (Oct 3, expire_due_on_apply): an apply landing
    # on a DUE box used to re-arm it — three fresh rotations for a spent
    # guarantee (the Oct 3 08:00 race: the apply beat the heartbeat's
    # tick by moments and a box one boundary from death got three more).
    # The box's business runs FIRST: a due box expires right here — the
    # SAME drift-aware expiry the heartbeat runs, so whichever fires
    # first wins and both converge — and the apply then evaluates
    # against the post-expiry factor, never stacking onto a dead
    # guarantee. Runs before the guards: the guarantee's death is the
    # box's business, not the cap's or the dedupe's.
    _due = st.get("auto_adjust_box")
    if isinstance(_due, dict) and int(_due.get("rotations_left") or 0) <= 0:
        expire_if_due()
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
            # THE CARRY (Oct 3, the re-arm decision's other half): a stack
            # rides the box's REMAINING countdown — rotations_left is the
            # box's life, fixed at birth, never the apply's reset. A fresh
            # box (none prior) arms the full ADJUST_DURATION_ROTATIONS.
            _prior_box = st.get("auto_adjust_box")
            _carry = (int(_prior_box["rotations_left"])
                      if isinstance(_prior_box, dict)
                      and _prior_box.get("rotations_left") is not None
                      else ADJUST_DURATION_ROTATIONS)
            _carry = max(1, _carry)
            # THE WRITE-BACK REVALIDATION (the gate's [high] on the re-arm
            # decision): the HTTP apply runs beside the scan thread, so the
            # snapshot above can be outdated at write time by a concurrent
            # heartbeat tick/expiry or a foreign factor writer. The FRESH
            # state decides: the box's remaining rotations at write time
            # win; a box that died in the window makes this a BIRTH; a
            # factor that moved re-clamps the stack from the live value —
            # the rails' base is the truth, never the snapshot.
            _now_box = pg._state().get("auto_adjust_box")
            if (isinstance(_now_box, dict)
                    and _now_box.get("rotations_left") is not None
                    and int(_now_box["rotations_left"] or 0) <= 0):
                # THE DUE BOX AT THE REVALIDATION (the gate's rot-0 [high]
                # on 1b31271): the max(1, rot) clamp turned a box TICKED
                # TO 0 inside the window back into a live one-rotation
                # guarantee — stacking onto a dead box. The box's
                # business runs here: the same drift-aware expiry the
                # heartbeat runs, then the apply evaluates as a birth
                # from the POST-expiry factor. `st` is re-read — the
                # expiry's ledger row must ride the live list, never a
                # stale snapshot's.
                expire_if_due()
                st = pg._state()
                _now_box = st.get("auto_adjust_box")
            if isinstance(_now_box, dict) and _now_box.get("rotations_left") is not None:
                _carry = max(1, int(_now_box["rotations_left"]))
                _live = read_guard_factor()
                if abs(float(_now_box.get("after") or 0.0) - float(_live)) > 1e-9:
                    before = _live
                    after = clamp(before, delta)
                    rec.update({"before": before, "after": after})
            else:
                _carry = ADJUST_DURATION_ROTATIONS
                before = read_guard_factor()
                after = clamp(before, delta)
                rec.update({"before": before, "after": after})
            rec.update({"status": "applied", "after": after,
                        "reason": "ok",
                        "expires_after_rotations": _carry})
            _ledger(st).append(rec)
            # OWNER-KEY SAVES (round-2): land the ledger FIRST — the
            # setter re-reads, two writers never clobber — then the
            # setter writes its own fresh view, then only the box rides
            # the last save. Never a full stale snapshot.
            pg._save({"auto_adjust_ledger": _ledger(st)})
            # THE WRITE-TIME WINDOW (the gate's non-atomic-revalidation
            # [high]): the setter's own read decides the countdown now —
            # the number passed here is only a BIRTH's window, so a tick
            # landing inside the write's window can never be overwritten
            # with this snapshot's carry.
            _wrote = set_guard_factor_temporarily(
                after, ADJUST_DURATION_ROTATIONS, before=before)
            box = pg._state().get("auto_adjust_box") or {}   # re-read:
            if _wrote.get("expired_due_box"):
                # THE SETTER BURIED A DUE BOX AT THE WRITE (the
                # third-order window): the rec's story was built on the
                # pre-expiry base — the post-expiry anchor is the truth.
                # Re-clamp from it so the rec, the box and the factor
                # tell one story (R3's rule, at the write); the ledger
                # row is corrected IN the file's live list — the expiry
                # landed in the setter's own read, and overlaying this
                # call's stale ledger would erase it.
                before = float(box.get("before") or before)
                after = clamp(before, delta)
                _win = int(box.get("rotations_left")
                           or ADJUST_DURATION_ROTATIONS)
                box.update({"after": after, "rotations_left": _win})
                rec.update({"before": before, "after": after,
                            "expires_after_rotations": _win})
                _fresh = pg._state()
                for _r in _ledger(_fresh):
                    if (_r.get("rotation_id") == rotation_id
                            and _r.get("status") == "applied"):
                        _r.update(rec)
                        break
                box.update({"rotation_id": rotation_id,
                            "evidence": evidence_checksum})
                pg._save({"factor": after, "auto_adjust_box": box,
                          "auto_adjust_ledger": _ledger(_fresh)})
                return rec
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
    `before` and mark the row reverted. Undoing the rotation that OWNS
    the live box collapses it (the full undo); undoing a rotation that
    STACKED onto a live box leaves the box alive under its ORIGINAL
    anchor — popping it would strand every earlier loosening forever.
    A rollback with NO live box refuses (time_box_already_cleared):
    after the full undo, a stacked rotation's stale undo must not
    resurrect its intermediate factor outside any box."""
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
    # THE BOXLESS SLATE (Copilot [high] on 8820eb4, verified red): after
    # the full undo collapsed the box, a later stale undo of one of its
    # stacked rows would still "revert" — parking a stale intermediate
    # factor forever outside any box (and only half-saved, ledger
    # logicless). Rollback is a time-box operation: with no live box
    # there is nothing to undo an adjust into — refuse by name.
    box = st.get("auto_adjust_box") or None
    if box is None:
        return {"status": "refused", "reason": "time_box_already_cleared"}
    # THE LINEAGE GATE (Copilot [high] on dca35ae, verified red): the
    # ledger outlives boxes, so shape alone is never ownership — a
    # cleared chain's row can share the live box's anchor or after
    # EXACTLY. Membership is the live box's own FACTOR-CONTIGUOUS
    # chain: walk the applied rows backwards from the box's after,
    # each row's before becoming the next factor, until the anchor —
    # ledger order IS lineage (stacks land in order). A cleared
    # chain's same-shaped row sits BELOW that walk and never enters
    # it; anything outside the walk refuses by name.
    _chain: list[dict] = []
    _cur = float(box.get("after") or 0.0)
    _anchor = float(box.get("before") or 0.0)
    for _r in reversed(_ledger(st)):
        if _r.get("status") != "applied":
            continue
        if abs(float(_r.get("after") or 0.0) - _cur) < 1e-9:
            _chain.append(_r)
            _cur = float(_r.get("before") or 0.0)
            if abs(_cur - _anchor) < 1e-9:
                break
    if not any(str(r.get("rotation_id") or "") == rotation_id
               for r in _chain):
        return {"status": "refused", "reason": "rotation_not_in_live_box"}
    st["factor"] = before
    # THE ROLLBACK-STRANDING HOLE (Oct 3 review): the box used to be
    # popped UNCONDITIONALLY — undoing the top of a stack destroyed the
    # ORIGINAL anchor and stranded every earlier loosening forever
    # (dormant in the scanner, one hop from the hindsight API's POST
    # /rollback). Ownership is decided by the ANCHOR, never by the box's
    # rotation_id (every stacked apply re-stamps it): a row's before
    # equals the box's before only when that row opened the box (or the
    # box's loosening was already healed), so only that undo collapses
    # it; a stacked undo leaves the box alive and rewinds its lineage
    # to the rotation the undo lands on.
    _collapses = abs(float(box.get("before") or 0.0) - before) < 1e-9
    if _collapses:
        st.pop("auto_adjust_box", None)
        st["auto_adjust_box"] = None   # None-means-delete under merge-save
        _save_box = None
    else:
        _save_box = box
        if box is not None:
            # the surviving loosening: the chain row that LANDED at the
            # factor this undo just restored (inside the live chain the
            # walk just proved this rotation belongs to — an after-match
            # over the whole ledger would name some earlier day's
            # same-shaped rotation)
            _prev = [r for r in _chain
                     if abs(float(r.get("after") or 0.0) - before) < 1e-9]
            if _prev:
                box["after"] = before
                box["rotation_id"] = str(_prev[-1].get("rotation_id") or "")
                box["evidence"] = str(_prev[-1].get("evidence") or "")
    _ledger(st).append({
        "day": _today_key(now), "rotation_id": rotation_id,
        "status": "reverted", "before": float(row["after"]),
        "after": before, "delta": 0.0, "reason": "operator_rollback",
        "evidence": str(row.get("evidence") or ""),
        "applied_at": now.isoformat(),
    })
    # OWNER-KEY SAVE (round-2): factor + ledger + the box (the
    # tombstone on a collapse, the surviving box on a stacked undo).
    pg._save({"factor": st["factor"],
              "auto_adjust_ledger": st["auto_adjust_ledger"],
              "auto_adjust_box": _save_box})
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
