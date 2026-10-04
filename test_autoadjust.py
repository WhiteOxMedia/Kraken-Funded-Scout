"""THE AUTO-ADJUST DRIVE (task 2): the rule's edges, every rail's
teeth, the time-box revert, and the evidence bundle's immutability —
the profit_guard state sandboxed through its own explicit-override
seam (the Sep-24 pattern), production files never touched."""
import json
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import auto_adjust as aa
import profit_guard as pg

results: list[tuple[str, bool, object]] = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


# ── the sandbox: profit_guard's explicit-override state seam ────────
_sbx = Path(tempfile.mkdtemp())
_sbx_state = _sbx / "meta_layers_state.json"
_sbx_state.write_text(json.dumps({"factor": 2.0}), encoding="utf-8")
_real_state_file = getattr(pg, "STATE_FILE", None)
pg.STATE_FILE = _sbx_state            # the Sep-24 explicit-override path
try:

    # ── the rule, pure ──────────────────────────────────────────────
    check("below the sample floor the rule refuses (named, not silent)",
          aa.compute_delta(4, 4) == (0.0, "insufficient_samples"),
          aa.compute_delta(4, 4))
    check("below the ratio floor the rule refuses (the gates were RIGHT)",
          aa.compute_delta(20, 4) == (0.0, "low_ratio"),
          aa.compute_delta(20, 4))
    d5, why5 = aa.compute_delta(5, 2)
    check("B_MIN boundary passes and the delta is proportional "
          "(0.05 x 0.4 x ln 6)",
          why5 == "ok" and abs(d5 - (-(0.05 * 0.4) * aa.__dict__["math"].log(6)))
          < 1e-9, (d5, why5))
    d200, _ = aa.compute_delta(200, 200)
    check("the per-adjustment cap binds (r=1.0, B=200 would beg -0.265, "
          "it gets -0.10)", d200 == -aa.DELTA_MAX, d200)
    dB, _ = aa.compute_delta(10 ** 6, 10 ** 6)
    check("the cap holds at absurd sample counts", dB == -aa.DELTA_MAX, dB)

    check("the clamp honors the machine's guard bounds (2.0 -> 1.92)",
          aa.clamp(2.0, -0.08) == 1.92, aa.clamp(2.0, -0.08))
    check("the clamp floors at GUARD_CLAMP's low end (0.6 - 0.2 -> 0.5)",
          aa.clamp(0.6, -0.2) == 0.5, aa.clamp(0.6, -0.2))

    check("concentration: one gate carrying 8 of 10 payers reads 0.8",
          abs(aa.concentration([{"P_g": 8}, {"P_g": 2}]) - 0.8) < 1e-9,
          aa.concentration([{"P_g": 8}, {"P_g": 2}]))
    check("concentration of no payers is 0.0, never a division error",
          aa.concentration([{"P_g": 0}, {"P_g": 0}]) == 0.0,
          aa.concentration([{"P_g": 0}]))

    # ── the integration: a simulated rotation, rails to teeth ──────
    now = datetime.now().astimezone()
    per_gate_ok = [{"gate": "tide-out", "B_g": 4, "P_g": 3},
                   {"gate": "deep-veto", "B_g": 4, "P_g": 3},
                   {"gate": "stale-move", "B_g": 2, "P_g": 2}]

    rec = aa.apply_adjustment(-0.08, "SIM-1", "sha256:sim1",
                              per_gate=per_gate_ok)
    check("a clean rotation APPLIES (conc 3/8 < 0.5, factor 2.0 -> 1.92)",
          rec["status"] == "applied" and rec["after"] == 1.92
          and aa.read_guard_factor() == 1.92, (rec, aa.read_guard_factor()))
    box = json.loads(_sbx_state.read_text())["auto_adjust_box"]
    check("the time box rides the state (3 rotations, before recorded)",
          box["rotations_left"] == 3 and box["before"] == 2.0
          and box["evidence"] == "sha256:sim1", box)

    rec2 = aa.apply_adjustment(-0.08, "SIM-2", "sha256:sim2",
                               per_gate=per_gate_ok)
    rec3 = aa.apply_adjustment(-0.08, "SIM-3", "sha256:sim3",
                               per_gate=per_gate_ok)
    check("the daily cumulative cap refuses the third same-day adjust "
          "(0.16 + 0.08 > 0.20)",
          rec3["status"] == "refused"
          and rec3["reason"] == "daily_cap_exceeded", rec3)
    check("the refusals are LEDGERED, not dropped (three rows, named)",
          len(pg._state()["auto_adjust_ledger"]) == 3
          and pg._state()["auto_adjust_ledger"][-1]["status"] == "refused",
          pg._state()["auto_adjust_ledger"])

    # concentration blocks AUTO-apply, queues for review instead
    conc_gate = [{"gate": "deep-veto", "B_g": 8, "P_g": 8},
                 {"gate": "tide-out", "B_g": 2, "P_g": 0}]
    aa.DAILY_CAP = 10.0   # widen the day so the conc path is the one tested
    rec4 = aa.apply_adjustment(-0.05, "SIM-4", "sha256:sim4",
                               per_gate=conc_gate, auto=True)
    check("a single-gate story cannot auto-apply — it queues for review "
          "and the factor does not move",
          rec4["status"] == "queued"
          and rec4["reason"] == "concentration_gate_blocks_auto_apply"
          and aa.read_guard_factor() == 1.84, (rec4, aa.read_guard_factor()))
    # queue_review override: the AUTHENTICATED operator can force it
    # through the rails (auto=False is a review request, NOT a bypass —
    # the isolated review's major, restated as the rule)
    rec5 = aa.apply_adjustment(-0.05, "SIM-4", "sha256:sim4",
                               per_gate=conc_gate, auto=False,
                               override_concentration=True)
    aa.DAILY_CAP = 0.20
    check("the operator's queue_review override applies through the "
          "same caps (1.84 -> 1.79)",
          rec5["status"] == "applied" and aa.read_guard_factor() == 1.79,
          (rec5, aa.read_guard_factor()))

    # rollback restores the applied row's before
    rb = aa.rollback("SIM-4")
    check("the operator rollback restores the pre-adjust factor",
          rb["status"] == "reverted" and rb["restored_to"] == 1.84
          and aa.read_guard_factor() == 1.84, (rb, aa.read_guard_factor()))
    check("rolling back a rotation with no applied row refuses honestly",
          aa.rollback("NO-SUCH")["status"] == "refused", aa.rollback("NO-SUCH"))

    # the stacked-box undo above PRESERVES the box (the Oct 3 fix), so
    # the setter's own pin needs a boxless sandbox: the operator's full
    # undo — the box-OPENING rotation, whose before IS the anchor —
    # collapses it (the collapse path, exercised early)
    _rb1 = aa.rollback("SIM-1")
    check("the full undo (the box-opening rotation) collapses the box "
          "and restores 2.0",
          _rb1["status"] == "reverted" and _rb1.get("restored_to") == 2.0
          and abs(aa.read_guard_factor() - 2.0) < 1e-9
          and not (pg._state().get("auto_adjust_box") or {}), _rb1)

    # the time-box auto-revert: a FRESH box, its own before, its own revert
    aa.set_guard_factor_temporarily(1.76, 3, before=1.84)
    for _ in range(3):
        aa.tick_rotation()
    exp = aa.expire_if_due()
    check("after 3 rotations the box expires and ITS before factor is "
          "restored (the auto-revert)",
          exp and exp.get("reverted_to") == 1.84
          and aa.read_guard_factor() == 1.84
          and "auto_adjust_box" not in pg._state(), (exp, aa.read_guard_factor()))
    check("a live box reports its remaining rotations instead of expiring",
          (aa.set_guard_factor_temporarily(1.9, 2, before=2.0),
           aa.tick_rotation(), aa.expire_if_due())[2]
          == {"rotations_left": 1}, aa.expire_if_due())
    aa.expire_if_due()   # the cleanup revert (to 2.0)

    # THE STACKED-BOX BEFORE (Sep 29 review major, red-first): a second
    # adjustment stacking onto a LIVE box must preserve the ORIGINAL
    # before — the box's `before` is the restore target, and replacing
    # it with the already-loosened factor would strand the first
    # loosening forever (two boxes = never fully restored).
    aa.set_guard_factor_temporarily(1.9, 2, before=2.0)   # box #1: 2.0 → 1.9
    aa.set_guard_factor_temporarily(1.8, 2)               # box #2 stacks
    _box2 = pg._state().get("auto_adjust_box") or {}
    check("a stacked adjustment preserves the ORIGINAL before in the "
          "box (the restore target is the pre-adjustment factor, not "
          "the already-loosened one)",
          abs(float(_box2.get("before") or 0) - 2.0) < 1e-9
          and abs(float(_box2.get("after") or 0) - 1.8) < 1e-9, _box2)
    for _ in range(2):
        aa.tick_rotation()   # run the stacked box to its expiry
    aa.expire_if_due()   # restores 2.0 through the stacked box
    check("the stacked box's expiry restores the ORIGINAL factor (2.0, "
          "not the intermediate)",
          abs(aa.read_guard_factor() - 2.0) < 1e-9, aa.read_guard_factor())

    # THE PRODUCTION SHAPE (Oct 2 review major, red-first): the pin
    # above feeds the setter explicitly — production NEVER does. The
    # real caller is apply_adjustment, and it always passes before=the
    # LIVE factor, which is already the box's after whenever a box is
    # up — so the stacked-box branch (which demanded before=None) was
    # dead code on the production path, and a second applied rotation
    # rewrote the box's ORIGINAL before to the already-adjusted
    # factor. Two stacked PRODUCTION applies must still expire to the
    # ORIGINAL factor, not the first box's after.
    aa.DAILY_CAP = 10.0   # the day's ledger is fat from the pins above
    _rs1 = aa.apply_adjustment(-0.05, "SIM-STACK1", "sha256:stack1")
    check("the first production apply lands (applied, 2.0 -> 1.95)",
          _rs1["status"] == "applied"
          and abs(aa.read_guard_factor() - 1.95) < 1e-9, _rs1)
    _rs2 = aa.apply_adjustment(-0.05, "SIM-STACK2", "sha256:stack2")
    check("the second production apply lands ON the live box (applied, "
          "1.95 -> 1.90 — the stack is real, never a refusal masquerading "
          "as a pass)",
          _rs2["status"] == "applied"
          and abs(aa.read_guard_factor() - 1.90) < 1e-9, _rs2)
    _pbox = pg._state().get("auto_adjust_box") or {}
    check("the production stack keeps the ORIGINAL before in the box "
          "(2.0 — the restore target is the pre-adjustment factor, not "
          "the first box's 1.95)",
          abs(float(_pbox.get("before") or 0) - 2.0) < 1e-9
          and abs(float(_pbox.get("after") or 0) - 1.90) < 1e-9, _pbox)
    for _ in range(3):
        aa.tick_rotation()   # run the stacked box to its expiry
    aa.expire_if_due()   # the stacked box's own revert
    check("the production stack's expiry restores the ORIGINAL factor "
          "(2.0, not the intermediate 1.95)",
          abs(aa.read_guard_factor() - 2.0) < 1e-9, aa.read_guard_factor())

    # THE LIFECYCLE (Oct 2 behavioral verify, folded in as permanent
    # drive legs): FOUR production-shape stacks — the real Sep-30 chain
    # depth — drained to expiry, then a post-expiry restart. The box's
    # whole life against the only caller production has. The cap stays
    # widened until the block's end (the day's ledger is already fat).
    aa.DAILY_CAP = 10.0
    _lf = 2.0
    for _i, _rid in enumerate(["LIFE-A", "LIFE-B", "LIFE-C", "LIFE-D"]):
        _rec = aa.apply_adjustment(-0.05, _rid, f"sha256:{_rid}")
        _lf = round(_lf - 0.05, 4)
        check(f"lifecycle stack #{_i + 1} ({_rid}) applies, factor {_lf}",
              _rec["status"] == "applied"
              and abs(aa.read_guard_factor() - _lf) < 1e-9, _rec)
        check(f"lifecycle stack #{_i + 1}: the box's anchor is still the "
              "ORIGINAL 2.0 (deep stacks never rewrite the restore target)",
              abs(float((pg._state().get("auto_adjust_box") or {})
                        .get("before") or 0) - 2.0) < 1e-9,
              pg._state().get("auto_adjust_box"))
    check("after FOUR stacks the box's after is the last apply (1.80)",
          abs(float((pg._state().get("auto_adjust_box") or {}).get("after")
                    or 0) - 1.80) < 1e-9,
          pg._state().get("auto_adjust_box"))

    for _ in range(3):
        aa.tick_rotation()   # drain the 4-stack box
    _lexp = aa.expire_if_due()
    check("the 4-stack box's expiry restores the ORIGINAL 2.0",
          bool(_lexp) and _lexp.get("reverted_to") == 2.0
          and abs(aa.read_guard_factor() - 2.0) < 1e-9,
          (_lexp, aa.read_guard_factor()))
    check("the expiry LEDGERS its revert (a time_box_expired row: "
          "1.80 -> 2.0, LIFE-D's rotation_id carried)",
          any(r.get("reason") == "time_box_expired"
              and r.get("status") == "reverted"
              and abs(float(r.get("before") or 0) - 1.80) < 1e-9
              and abs(float(r.get("after") or 0) - 2.0) < 1e-9
              and r.get("rotation_id") == "LIFE-D"
              for r in pg._state().get("auto_adjust_ledger") or []),
          [r for r in pg._state().get("auto_adjust_ledger") or []
           if r.get("reason") == "time_box_expired"])
    check("expiry removes the box from the state (the tombstone held)",
          not (pg._state().get("auto_adjust_box") or {}),
          pg._state().get("auto_adjust_box"))

    _lfresh = aa.apply_adjustment(-0.05, "LIFE-E", "sha256:LIFE-E")
    _lbox = pg._state().get("auto_adjust_box") or {}
    check("the post-expiry apply opens a FRESH box anchored at 2.0 "
          "(no stale carry; its own rotation_id and evidence)",
          _lfresh["status"] == "applied"
          and abs(float(_lbox.get("before") or 0) - 2.0) < 1e-9
          and _lbox.get("rotation_id") == "LIFE-E"
          and _lbox.get("evidence") == "sha256:LIFE-E", (_lfresh, _lbox))
    for _ in range(3):
        aa.tick_rotation()   # drain the fresh box
    aa.expire_if_due()   # the fresh box reverts on its own terms
    check("the post-expiry box reverts to 2.0 and leaves no box behind",
          abs(aa.read_guard_factor() - 2.0) < 1e-9
          and not (pg._state().get("auto_adjust_box") or {}),
          (aa.read_guard_factor(), pg._state().get("auto_adjust_box")))
    aa.DAILY_CAP = 0.20

    # THE DOUBLE-APPLY GUARD (Sep 29 review major, red-first): an
    # `applied` row for the SAME rotation refuses a second apply —
    # the boundary hook self-heals per tick and a cadence retry must
    # never double-loosen the guard. (The cap is widened FIRST so the
    # pin exercises the dedupe, not the cap.)
    aa.DAILY_CAP = 10.0
    rec_d1 = aa.apply_adjustment(-0.05, "SIM-DOUBLE", "sha256:dbl")
    check("the first apply for a rotation lands (applied)",
          rec_d1["status"] == "applied", rec_d1)
    rec_d2 = aa.apply_adjustment(-0.05, "SIM-DOUBLE", "sha256:dbl")
    check("a second apply for the SAME rotation refuses by name "
          "(rotation_already_applied — one adjustment per rotation, "
          "ever)",
          rec_d2["status"] == "refused"
          and rec_d2["reason"] == "rotation_already_applied", rec_d2)
    aa.rollback("SIM-DOUBLE")
    aa.DAILY_CAP = 0.20

    # THE ROLLBACK-STRANDING HOLE (Oct 3 review, red-first): the
    # operator's rollback popped the box UNCONDITIONALLY — undoing the
    # top of a stack silently destroyed the ORIGINAL anchor, stranding
    # every earlier loosening forever. The path is dormant in the
    # scanner but one hop from the live hindsight API's POST /rollback,
    # so the inconsistency is pinned here: a rollback of a STACKED
    # rotation must keep the box alive under its ORIGINAL anchor; only
    # undoing the BASE rotation collapses the whole box.
    aa.DAILY_CAP = 10.0   # the day's ledger is fat from the pins above
    _rbA = aa.apply_adjustment(-0.05, "STACK-A", "sha256:stacka")
    check("the base rotation opens its box (applied, 2.0 -> 1.95)",
          _rbA["status"] == "applied"
          and abs(aa.read_guard_factor() - 1.95) < 1e-9, _rbA)
    _rbB = aa.apply_adjustment(-0.05, "STACK-B", "sha256:stackb")
    check("the stacked rotation lands on the base box (applied, "
          "1.95 -> 1.90)",
          _rbB["status"] == "applied"
          and abs(aa.read_guard_factor() - 1.90) < 1e-9, _rbB)
    _rb2 = aa.rollback("STACK-B")
    check("rolling back the SECOND stacked rotation lands (reverted, "
          "1.90 -> 1.95 — the first adjustment's loosening legitimately "
          "stands)",
          _rb2["status"] == "reverted"
          and _rb2.get("restored_to") == 1.95
          and abs(aa.read_guard_factor() - 1.95) < 1e-9, _rb2)
    _rbbox = pg._state().get("auto_adjust_box") or {}
    check("the rollback of a STACKED rotation keeps the box alive with "
          "the ORIGINAL anchor (2.0) — undoing the top never strands "
          "the first adjustment's restore target",
          abs(float(_rbbox.get("before") or 0) - 2.0) < 1e-9
          and int(_rbbox.get("rotations_left") or 0)
          == aa.ADJUST_DURATION_ROTATIONS
          and abs(float(_rbbox.get("after") or 0) - 1.95) < 1e-9
          and _rbbox.get("rotation_id") == "STACK-A", _rbbox)
    for _ in range(aa.ADJUST_DURATION_ROTATIONS):
        aa.tick_rotation()   # drain the surviving box
    _rbexp = aa.expire_if_due()
    check("the surviving loosening is still temporary by construction: "
          "the drained box's expiry restores the ORIGINAL 2.0 and the "
          "expiry row names the box's lineage (STACK-A)",
          bool(_rbexp) and _rbexp.get("reverted_to") == 2.0
          and abs(aa.read_guard_factor() - 2.0) < 1e-9
          and any(r.get("reason") == "time_box_expired"
                  and r.get("rotation_id") == "STACK-A"
                  for r in pg._state().get("auto_adjust_ledger") or []),
          (_rbexp, aa.read_guard_factor()))
    _rbC = aa.apply_adjustment(-0.05, "STACK-C", "sha256:stackc")
    check("the base rotation opens its own box for the collapse leg "
          "(applied, 2.0 -> 1.95)",
          _rbC["status"] == "applied"
          and abs(aa.read_guard_factor() - 1.95) < 1e-9, _rbC)
    _rb3 = aa.rollback("STACK-C")   # the BASE rotation: the full undo
    check("rolling back the BASE rotation collapses the whole box "
          "(factor 2.0, no box left — the operator's full undo)",
          _rb3["status"] == "reverted"
          and _rb3.get("restored_to") == 2.0
          and abs(aa.read_guard_factor() - 2.0) < 1e-9
          and not (pg._state().get("auto_adjust_box") or {}), _rb3)
    _rb4 = aa.rollback("STACK-B")   # the stale offer: the box is GONE
    check("a stacked rotation's undo REFUSES after the full undo cleared "
          "the box (time_box_already_cleared — no stale intermediate "
          "factor resurrects outside any box; the factor stays 2.0)",
          _rb4["status"] == "refused"
          and _rb4.get("reason") == "time_box_already_cleared"
          and abs(aa.read_guard_factor() - 2.0) < 1e-9, _rb4)
    # THE STALE-ROW GATE (Copilot [high] on dca35ae, red-first): a same-
    # shaped applied row from the CLEARED chain still sits in the ledger
    # — rollback must decide by LINEAGE (the live box's contiguous
    # chain), never by factor shape, or a dead rotation's undo collapses
    # the live box or rewires it to a foreign chain.
    _rbD = aa.apply_adjustment(-0.05, "STACK-D", "sha256:stackd")
    check("a fresh chain's box opens on the cleared slate (applied, "
          "2.0 -> 1.95)",
          _rbD["status"] == "applied"
          and abs(aa.read_guard_factor() - 1.95) < 1e-9, _rbD)
    _rb5 = aa.rollback("STACK-A")   # cleared chain's BASE: before == the LIVE anchor
    _rbd = pg._state().get("auto_adjust_box") or {}
    check("a cleared chain's row CANNOT undo into the live box even when "
          "its before equals the live anchor (rotation_not_in_live_box — "
          "the stale undo must not collapse the live box)",
          _rb5["status"] == "refused"
          and _rb5.get("reason") == "rotation_not_in_live_box"
          and abs(aa.read_guard_factor() - 1.95) < 1e-9
          and _rbd.get("rotation_id") == "STACK-D"
          and abs(float(_rbd.get("before") or 0) - 2.0) < 1e-9, _rb5)
    _rb6 = aa.rollback("STACK-B")   # cleared chain's stack: before == the LIVE after
    check("a cleared chain's row refuses even when its before matches "
          "the live box's after (shape is not lineage — the box stays "
          "intact under its own chain)",
          _rb6["status"] == "refused"
          and _rb6.get("reason") == "rotation_not_in_live_box"
          and (pg._state().get("auto_adjust_box") or {}).get("rotation_id")
          == "STACK-D", _rb6)
    _rb7 = aa.rollback("STACK-D")   # the live chain's own full undo
    check("the live chain's own base undo still collapses its box "
          "(reverted 2.0, boxless — lineage passes, shape governs)",
          _rb7["status"] == "reverted"
          and _rb7.get("restored_to") == 2.0
          and abs(aa.read_guard_factor() - 2.0) < 1e-9
          and not (pg._state().get("auto_adjust_box") or {}), _rb7)
    aa.DAILY_CAP = 0.20

    # THE TIME-BOX GOES LIVE (Oct 3, the wiring decision): the box's
    # heartbeat — one tick per closed rotation — is wired into the
    # scanner's boundary hook, CANARY-INDEPENDENT (the box's lifecycle
    # must not ride the cohort's arm state), watermarked in
    # scanner_state. And expiry is DRIFT-AWARE: the nightly cooldown
    # recompute may move the factor while a box is live, and restoring
    # a stale anchor over a fresher recompute would yank the guard
    # against newer data — a drifted box retires WITHOUT the restore,
    # ledgered by name.
    _bs_src = (BASE / "background_scanner.py").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    check("the scanner's boundary hook carries the box heartbeat (one "
          "tick per closed rotation + expire, watermarked "
          "aa_box_last_rotation — the wiring is fenced in source)",
          "aa_box_last_rotation" in _bs_src
          and "_aa_hb.tick_rotation()" in _bs_src
          and "_aa_hb.expire_if_due()" in _bs_src, "")
    aa.DAILY_CAP = 10.0
    aa.apply_adjustment(-0.05, "WIRE-1", "sha256:wire1")   # box: 2.0 -> 1.95
    pg._save({"factor": 1.7})   # the recompute's move: a foreign writer
    for _ in range(3):
        aa.tick_rotation()
    _wexp = aa.expire_if_due()
    check("the DRIFTED box retires WITHOUT the restore (the recompute's "
          "1.7 stands — a stale anchor never yanks the guard against "
          "newer data)",
          bool(_wexp) and _wexp.get("superseded") is True
          and abs(float(_wexp.get("factor") or 0) - 1.7) < 1e-9
          and abs(aa.read_guard_factor() - 1.7) < 1e-9
          and not (pg._state().get("auto_adjust_box") or {}), _wexp)
    _wrows = [r for r in pg._state().get("auto_adjust_ledger") or []
              if r.get("reason") == "time_box_expired_stale_anchor"]
    check("the drifted expiry LEDGERS its retirement (status expired, "
          "1.95 -> 1.7 — the no-restore is on the record)",
          len(_wrows) == 1 and _wrows[0].get("status") == "expired"
          and abs(float(_wrows[0].get("before") or 0) - 1.95) < 1e-9
          and abs(float(_wrows[0].get("after") or 0) - 1.7) < 1e-9
          and _wrows[0].get("rotation_id") == "WIRE-1", _wrows)
    aa.apply_adjustment(-0.05, "WIRE-2", "sha256:wire2")   # 1.7 -> 1.65
    for _ in range(3):
        aa.tick_rotation()
    _cexp = aa.expire_if_due()
    check("the UNDRIFTED box still restores its anchor (the clean "
          "auto-revert, unchanged by the guard)",
          bool(_cexp) and _cexp.get("reverted_to") == 1.7
          and abs(aa.read_guard_factor() - 1.7) < 1e-9
          and not (pg._state().get("auto_adjust_box") or {}), _cexp)
    aa.DAILY_CAP = 0.20

    # ── THE PARAMS SEAM (Sep 29, red-first): the armed cell governs the
    #    CAP for real — a tiny armed daily_cap refuses where the module
    #    default (0.20) would apply, and the record carries the cell it
    #    evaluated under.
    aa.DAILY_CAP = 10.0   # the module default is NOT the guard here
    rec_p = aa.apply_adjustment(-0.05, "SIM-CELL", "sha256:cell",
                                params={"daily_cap": 0.02})
    check("the ARMED daily_cap bites: a 0.05 delta refuses under a "
          "0.02 armed cap even though the module default is 10.0 (the "
          "cell governs, not the module global)",
          rec_p["status"] == "refused"
          and rec_p["reason"] == "daily_cap_exceeded"
          and rec_p["params"]["daily_cap"] == 0.02
          and rec_p["params"]["alpha"] == aa.ALPHA, rec_p)
    # (the full-cell leg's armed cap is generous on purpose: the day's
    # ledger already carries the earlier pins' |deltas| — this leg
    # proves the record, the refusal leg above proved the cap's teeth)
    rec_q = aa.apply_adjustment(-0.05, "SIM-CELL2", "sha256:cell2",
                                params={"alpha": 0.03, "delta_max": 0.05,
                                        "daily_cap": 5.0})
    check("a full armed cell applies and the record carries ALL THREE "
          "keys (the proof the armed params are live)",
          rec_q["status"] == "applied"
          and rec_q["params"] == {"alpha": 0.03, "delta_max": 0.05,
                                  "daily_cap": 5.0}, rec_q)
    aa.rollback("SIM-CELL2")
    aa.DAILY_CAP = 0.20

    # non-negative deltas are not self-adjust
    rec6 = aa.apply_adjustment(0.05, "SIM-5", "sha256:sim5")
    check("a tightening delta is refused by name (self-adjust only "
          "refunds)", rec6["status"] == "refused"
          and rec6["reason"] == "non_negative_delta_is_not_self_adjust", rec6)

    # ── the evidence bundle (the day's cap widened: these test the
    #    bundle lifecycle, not the cap — the cap's teeth are proven above)
    aa.DAILY_CAP = 10.0
    aa.EVIDENCE_DIR = _sbx / "artifacts" / "auto_adjust"
    prop = aa.propose_from_aggregate("SIM-ROT-1", 3, 3, per_gate_ok)
    man = json.loads((Path(prop["bundle"]) / "manifest.json")
                     .read_text(encoding="utf-8"))   # leak-audit: ok (the bundle path is EVIDENCE_DIR-sandboxed above; the receiver carries the sandbox)
    check("a refused rotation still leaves its evidence bundle "
          "(insufficient_samples, checksummed)",
          prop["status"] == "refused"
          and prop["reason"] == "insufficient_samples"
          and man["bundle_checksum"].startswith("sha256:")
          and man["commit_sha"], (prop, man.get("commit_sha")))
    before_manifest = man["bundle_checksum"]
    prop2 = aa.propose_from_aggregate("SIM-ROT-1", 3, 3, per_gate_ok)
    man2 = json.loads((Path(prop2["bundle"]) / "manifest.json")
                      .read_text(encoding="utf-8"))   # leak-audit: ok (same sandboxed bundle path)
    check("the bundle is immutable — re-proposing never rewrites it",
          man2["bundle_checksum"] == before_manifest
          and prop2.get("evidence") == before_manifest,
          (before_manifest, man2.get("bundle_checksum")))
    check("the bundle carries the per-gate CSV",
          (Path(prop["bundle"]) / "per_gate_ratios.csv").exists()
          and (Path(prop["bundle"]) / "summary.json").exists(),
          sorted(p.name for p in Path(prop["bundle"]).iterdir()))
    prop3 = aa.propose_from_aggregate(
        "SIM-ROT-2", 8, 6,
        [{"gate": "tide-out", "B_g": 4, "P_g": 3},
         {"gate": "deep-veto", "B_g": 4, "P_g": 3}])
    check("a passing rotation applies through propose_from_aggregate "
          "with the bundle attached",
          prop3["status"] == "applied" and prop3["delta"] < 0
          and Path(prop3["bundle"]).exists(), prop3)
    aa.rollback("SIM-ROT-2")
    aa.DAILY_CAP = 0.20

    # ── THE RE-ARM DECISION (Oct 3, expire_due_on_apply): an apply used
    #    to RE-ARM the box — rotations_left reset to 3 on every stack, so
    #    every loosening extended every earlier one and the bundle lived
    #    as long as applies kept landing (the Oct 3 08:00 race: the apply
    #    beat the heartbeat's tick and a box one boundary from death got
    #    three fresh rotations). The contract is CARRY: the countdown is
    #    the BOX's, fixed at birth; a stack rides the same remaining
    #    rotations, and an apply landing on a DUE box expires it FIRST
    #    (the same drift-aware expiry the heartbeat runs — whichever
    #    fires first wins, both converge), so no loosening ever stacks
    #    onto a dead guarantee.
    _ra_cap = aa.DAILY_CAP
    aa.DAILY_CAP = 10.0
    try:
        pg._save({"factor": 2.0, "auto_adjust_box": None})   # own the slate
        aa.apply_adjustment(-0.05, "RA-A", "ev:ra-a")
        _b1 = pg._state()["auto_adjust_box"]
        check("re-arm: a FRESH box still arms the full 3 (the birth contract)",
              _b1["rotations_left"] == 3 and _b1["before"] == 2.0, _b1)
        aa.tick_rotation()                                   # 3 -> 2
        r_a2 = aa.apply_adjustment(-0.05, "RA-B", "ev:ra-b")
        _b2 = pg._state()["auto_adjust_box"]
        check("re-arm: a STACK CARRIES the countdown (rotations_left is "
              "the box's remaining life, never the apply's reset)",
              _b2["rotations_left"] == 2 and _b2["before"] == 2.0, _b2)
        check("re-arm: the stacked row records its TRUE expiry window "
              "(the rec's promise matches the carried box)",
              r_a2.get("expires_after_rotations") == 2, r_a2)
        aa.tick_rotation()
        aa.tick_rotation()               # 2 -> 1 -> 0 (due, unexpired — the race window)
        r_a3 = aa.apply_adjustment(-0.05, "RA-C", "ev:ra-c")
        _exp3 = [r for r in pg._state()["auto_adjust_ledger"]
                 if str(r.get("reason", "")).startswith("time_box_expired")
                 and r.get("rotation_id") == "RA-B"]
        check("re-arm: an apply on a DUE box expires it FIRST (the same "
              "drift-aware expiry the heartbeat runs — no loosening "
              "stacks onto a dead guarantee)",
              len(_exp3) == 1 and _exp3[0]["status"] == "reverted"
              and _exp3[0]["after"] == 2.0, _exp3)
        _b3 = pg._state().get("auto_adjust_box") or {}
        check("re-arm: the post-expiry apply opens a FRESH box from the "
              "restored anchor (before 2.0, full 3 rotations)",
              r_a3["status"] == "applied" and r_a3["before"] == 2.0
              and _b3.get("rotations_left") == 3
              and _b3.get("before") == 2.0, (r_a3, _b3))
        # the drifted due-box: expiry retires WITHOUT the restore (the
        # recompute's factor stands) and the apply evaluates from IT
        pg._save({"factor": 1.7})        # the recompute's value (a foreign writer)
        aa.tick_rotation()
        aa.tick_rotation()
        aa.tick_rotation()               # 3 -> 0
        r_a4 = aa.apply_adjustment(-0.05, "RA-D", "ev:ra-d")
        _exp4 = [r for r in pg._state()["auto_adjust_ledger"]
                 if r.get("reason") == "time_box_expired_stale_anchor"
                 and r.get("rotation_id") == "RA-C"]
        _b4 = pg._state().get("auto_adjust_box") or {}
        check("re-arm: a DUE box under a DRIFTED factor retires stale (no "
              "restore yank) and the apply evaluates from the recompute's "
              "factor — the foreign writer is never clobbered",
              len(_exp4) == 1 and _exp4[0]["status"] == "expired"
              and r_a4["before"] == 1.7 and _b4.get("before") == 1.7,
              (_exp4, r_a4.get("before"), _b4))
        # a REFUSED apply still runs the box's business first: the due
        # box expires even when the new loosening is denied
        aa.DAILY_CAP = 0.10              # today's ledger already carries this block's 0.20
        aa.tick_rotation()
        aa.tick_rotation()
        aa.tick_rotation()               # 3 -> 0
        r_a5 = aa.apply_adjustment(-0.05, "RA-E", "ev:ra-e")
        _exp5 = [r for r in pg._state()["auto_adjust_ledger"]
                 if str(r.get("reason", "")).startswith("time_box_expired")
                 and r.get("rotation_id") == "RA-D"]
        check("re-arm: a REFUSED apply still expires the due box (the "
              "guarantee's death is the box's business, not the cap's)",
              r_a5["status"] == "refused" and len(_exp5) == 1
              and pg._state().get("auto_adjust_box") is None
              and abs(pg._state()["factor"] - 1.7) < 1e-9,
              (r_a5, _exp5, pg._state().get("auto_adjust_box"),
               pg._state().get("factor")))
        aa.DAILY_CAP = 10.0    # P5's refusal cap must not leak into the R legs
        # ── THE WRITE-BACK REVALIDATION (the gate's [high] on the re-arm
        #    decision): the HTTP apply runs beside the scan thread, so the
        #    snapshot the decision was built on can be outdated at write
        #    time by a concurrent heartbeat tick/expiry. Each pin lands
        #    the concurrent write INSIDE the window (a _state stub whose
        #    second call performs it) and demands the FRESH state decide.
        #    R1: the box expired in the window — the apply is a BIRTH.
        pg._save({"factor": 2.0, "auto_adjust_box": None})
        aa.apply_adjustment(-0.05, "RA-F", "ev:ra-f")
        aa.tick_rotation()
        aa.tick_rotation()               # rot 1 — alive at snapshot, gone by write
        _rs1 = pg._state
        _c1 = {"n": 0}
        def _stale_state_r1():
            _c1["n"] += 1
            if _c1["n"] == 2:
                pg._save({"factor": 2.0, "auto_adjust_box": None})   # the heartbeat lands
            return _rs1()
        pg._state = _stale_state_r1
        try:
            r_r1 = aa.apply_adjustment(-0.05, "RA-R1", "ev:ra-r1")
        finally:
            pg._state = _rs1
        _br1 = pg._state().get("auto_adjust_box") or {}
        check("re-arm: a box that EXPIRED inside the apply's window stays "
              "dead — the write-back births a fresh box from the live "
              "factor, never resurrects the closed guarantee",
              r_r1["before"] == 2.0 and _br1.get("rotations_left") == 3
              and _br1.get("before") == 2.0, (r_r1, _br1))
        #    R2: a TICK landed in the window — the fresh countdown wins.
        pg._save({"factor": 2.0, "auto_adjust_box": None})
        aa.apply_adjustment(-0.05, "RA-G", "ev:ra-g")
        aa.tick_rotation()               # rot 2 at snapshot; 1 by write
        _rs2 = pg._state
        _c2 = {"n": 0}
        def _stale_state_r2():
            _c2["n"] += 1
            if _c2["n"] == 2:
                _b = pg._state().get("auto_adjust_box")
                if _b:   # the heartbeat ticks a LIVE box — never a boxless slate
                    _b["rotations_left"] = 1
                    pg._save({"auto_adjust_box": _b})   # the tick lands
            return _rs2()
        pg._state = _stale_state_r2
        try:
            r_r2 = aa.apply_adjustment(-0.05, "RA-R2", "ev:ra-r2")
        finally:
            pg._state = _rs2
        _br2 = pg._state().get("auto_adjust_box") or {}
        check("re-arm: a TICK inside the apply's window is not written "
              "back over — the box's remaining rotations at write time win",
              _br2.get("rotations_left") == 1, (r_r2, _br2))
        #    R3: a FOREIGN writer moved the factor in the window — the
        #    stack re-clamps from the live value (the rails' base is the
        #    truth; the box's birth anchor stays the stale guard's call).
        pg._save({"factor": 2.0, "auto_adjust_box": None})
        aa.apply_adjustment(-0.05, "RA-H", "ev:ra-h")
        aa.tick_rotation()               # rot 2, factor 1.95 at snapshot
        _rs3 = pg._state
        _c3 = {"n": 0}
        def _stale_state_r3():
            _c3["n"] += 1
            if _c3["n"] == 3:   # after the entry reads, at the write's own read
                pg._save({"factor": 1.7})   # the recompute lands mid-window
            return _rs3()
        pg._state = _stale_state_r3
        try:
            r_r3 = aa.apply_adjustment(-0.05, "RA-R3", "ev:ra-r3")
        finally:
            pg._state = _rs3
        _br3 = pg._state().get("auto_adjust_box") or {}
        check("re-arm: a factor MOVED inside the apply's window re-clamps "
              "the stack from the live value (the rec and the written "
              "factor tell the truth about their base)",
              r_r3["before"] == 1.7 and r_r3["after"] == 1.65
              and abs(pg._state()["factor"] - 1.65) < 1e-9,
              (r_r3, _br3, pg._state().get("factor")))
        #    R4 (the gate's rot-0 [high] on 1b31271): the revalidation's
        #    max(1, rot) clamp turned a box TICKED TO 0 inside the window
        #    back into a live one-rotation guarantee — stacking onto a
        #    spent box. The tick lands at the revalidation's own read;
        #    the apply must run the box's business (the drift-aware
        #    expiry, ledgered) and BIRTH from the restored anchor —
        #    never clamp 0 -> 1.
        pg._save({"factor": 2.0, "auto_adjust_box": None})
        aa.apply_adjustment(-0.05, "RA-J", "ev:ra-j")
        aa.tick_rotation()               # 3 -> 2
        aa.apply_adjustment(-0.05, "RA-K", "ev:ra-k")
        aa.tick_rotation()               # 2 -> 1 — one boundary from due
        _rs4 = pg._state
        _c4 = {"n": 0}
        def _stale_state_r4():
            _c4["n"] += 1
            if _c4["n"] == 3:   # the revalidation's own read — the tick lands
                _b = pg._state().get("auto_adjust_box")
                if _b:   # the heartbeat ticks a LIVE box — never a boxless slate
                    _b["rotations_left"] = 0
                    pg._save({"auto_adjust_box": _b})   # the tick lands
            return _rs4()
        pg._state = _stale_state_r4
        try:
            r_r4 = aa.apply_adjustment(-0.05, "RA-R4", "ev:ra-r4")
        finally:
            pg._state = _rs4
        _exp_r4 = [r for r in pg._state()["auto_adjust_ledger"]
                   if str(r.get("reason", "")).startswith("time_box_expired")
                   and r.get("rotation_id") == "RA-K"]
        _br4 = pg._state().get("auto_adjust_box") or {}
        check("re-arm: a box TICKED TO 0 inside the window stays dead — "
              "the apply expires it (the heartbeat's own expiry, ledgered) "
              "and births fresh from the restored anchor, never clamps "
              "0 -> 1 onto a spent guarantee",
              len(_exp_r4) == 1 and _exp_r4[0]["status"] == "reverted"
              and _exp_r4[0]["after"] == 2.0
              and r_r4["before"] == 2.0 and r_r4["after"] == 1.95
              and r_r4.get("expires_after_rotations") == 3
              and _br4.get("rotations_left") == 3
              and _br4.get("before") == 2.0
              and abs(pg._state()["factor"] - 1.95) < 1e-9,
              (r_r4, _exp_r4, _br4, pg._state().get("factor")))
        #    R5 (the gate's non-atomic-revalidation [high]): a tick landing
        #    BETWEEN the revalidation read and the setter's save can still
        #    be written back over — the countdown must come from the
        #    setter's OWN read (write-time truth), not the caller's
        #    snapshot. The tick lands at the setter's read; the written
        #    box carries the ticked value.
        pg._save({"factor": 2.0, "auto_adjust_box": None})
        aa.apply_adjustment(-0.05, "RA-L", "ev:ra-l")
        aa.tick_rotation()               # rot 2 at the revalidation; 1 by the write
        _rs5 = pg._state
        _c5 = {"n": 0}
        def _stale_state_r5():
            _c5["n"] += 1
            if _c5["n"] == 5:   # the setter's own write-time read
                _b = pg._state().get("auto_adjust_box")
                if _b:   # the heartbeat ticks a LIVE box — never a boxless slate
                    _b["rotations_left"] = 1
                    pg._save({"auto_adjust_box": _b})   # the tick lands
            return _rs5()
        pg._state = _stale_state_r5
        try:
            r_r5 = aa.apply_adjustment(-0.05, "RA-R5", "ev:ra-r5")
        finally:
            pg._state = _rs5
        _br5 = pg._state().get("auto_adjust_box") or {}
        check("re-arm: a tick landing INSIDE the write's own window is "
              "never written back over — the setter's read-time rotations "
              "win, not the caller's snapshot",
              _br5.get("rotations_left") == 1 and _br5.get("before") == 2.0
              and abs(pg._state()["factor"] - 1.90) < 1e-9,
              (r_r5, _br5, pg._state().get("factor")))
        #    R6 (the third-order window the write-time rule opens): a box
        #    one boundary from due at the revalidation can be TICKED TO 0
        #    inside the SETTER's own read — the old setter wrote the
        #    passed carry and re-armed a spent guarantee. The write-time
        #    rule runs the box's business right there: expire (ledgered,
        #    drift-aware), birth from the POST-expiry anchor, and the rec
        #    re-clamped to tell the same story.
        pg._save({"factor": 2.0, "auto_adjust_box": None})
        aa.apply_adjustment(-0.05, "RA-M", "ev:ra-m")
        aa.tick_rotation()               # 3 -> 2
        aa.apply_adjustment(-0.05, "RA-N", "ev:ra-n")
        aa.tick_rotation()               # 2 -> 1 — due by the write
        _rs6 = pg._state
        _c6 = {"n": 0}
        def _stale_state_r6():
            _c6["n"] += 1
            if _c6["n"] == 5:   # the setter's own write-time read — the tick lands
                _b = pg._state().get("auto_adjust_box")
                if _b:   # the heartbeat ticks a LIVE box — never a boxless slate
                    _b["rotations_left"] = 0
                    pg._save({"auto_adjust_box": _b})   # the tick lands
            return _rs6()
        pg._state = _stale_state_r6
        try:
            r_r6 = aa.apply_adjustment(-0.05, "RA-R6", "ev:ra-r6")
        finally:
            pg._state = _rs6
        _exp_r6 = [r for r in pg._state()["auto_adjust_ledger"]
                   if str(r.get("reason", "")).startswith("time_box_expired")
                   and r.get("rotation_id") == "RA-N"]
        _br6 = pg._state().get("auto_adjust_box") or {}
        check("re-arm: a box due AT THE WRITE dies there — the setter "
              "expires it (ledgered) and births from the restored anchor, "
              "and the rec re-clamps to the post-expiry truth (the rec, "
              "the box and the factor tell one story)",
              len(_exp_r6) == 1 and _exp_r6[0]["status"] == "reverted"
              and _exp_r6[0]["after"] == 2.0
              and r_r6["before"] == 2.0 and r_r6["after"] == 1.95
              and r_r6.get("expires_after_rotations") == 3
              and _br6.get("rotations_left") == 3
              and _br6.get("before") == 2.0
              and abs(pg._state()["factor"] - 1.95) < 1e-9,
              (r_r6, _exp_r6, _br6, pg._state().get("factor")))
        #    R7/R8 (the gate's [high] on 393ba92 — THE SAVE-TIME WINDOW):
        #    R5's seam lands the tick at the setter's READ; the last open
        #    window is BETWEEN that read and the setter's pg._save — the
        #    seam below lands the heartbeat INSIDE the save call itself
        #    (first _save after arming disarms, runs the heartbeat, then
        #    the real save), red-first against a setter whose
        #    revalidation is read-time only: the stale built snapshot
        #    would resurrect the dead box and clobber the post-expiry
        #    factor truth, because auto_adjust_box is an owner key.
        pg._save({"factor": 2.0, "auto_adjust_box": None})
        aa.apply_adjustment(-0.05, "RA-P", "ev:ra-p")   # box: 1.95, before 2.0, rot 3
        aa.tick_rotation()                              # 3 -> 2
        _real_save_r7 = pg._save
        _armed7 = {"on": True}
        def _save7(*a, **k):
            if _armed7["on"]:
                _armed7["on"] = False
                aa.tick_rotation()      # 2 -> 1: the tick lands IN the window
            return _real_save_r7(*a, **k)
        pg._save = _save7
        try:
            r_r7 = aa.set_guard_factor_temporarily(1.90, 2, before=1.95)
        finally:
            pg._save = _real_save_r7
        _br7 = pg._state().get("auto_adjust_box") or {}
        check("re-arm: a tick landing between the setter's read and its "
              "save is never written back over — the save-time guard "
              "carries the fresh countdown",
              _br7.get("rotations_left") == 1 and _br7.get("before") == 2.0
              and abs(pg._state()["factor"] - 1.90) < 1e-9,
              (r_r7, _br7, pg._state()["factor"]))
        pg._save({"factor": 2.0, "auto_adjust_box": None})
        aa.apply_adjustment(-0.05, "RA-Q", "ev:ra-q")
        aa.tick_rotation()
        aa.tick_rotation()                              # 3 -> 1: due by the save
        _real_save_r8 = pg._save
        _armed8 = {"on": True}
        def _save8(*a, **k):
            if _armed8["on"]:
                _armed8["on"] = False
                aa.tick_rotation()      # 1 -> 0: the tick lands...
                aa.expire_if_due()      # ...and the expiry with it (the
                                        # heartbeat's own back-to-back)
            return _real_save_r8(*a, **k)
        pg._save = _save8
        try:
            r_r8 = aa.set_guard_factor_temporarily(1.90, 2, before=1.95)
        finally:
            pg._save = _real_save_r8
        _exp_r8 = [r for r in pg._state()["auto_adjust_ledger"]
                   if str(r.get("reason", "")).startswith("time_box_expired")
                   and r.get("rotation_id") == "RA-Q"]
        _br8 = pg._state().get("auto_adjust_box") or {}
        check("re-arm: an expiry landing between the setter's read and "
              "its save holds — the save-time guard buries the box first "
              "(ledgered) and births from the restored anchor, and "
              "expired_due_box tells the caller",
              len(_exp_r8) == 1 and _exp_r8[0]["status"] == "reverted"
              and _exp_r8[0]["after"] == 2.0
              and _br8.get("rotations_left") == 2
              and _br8.get("before") == 2.0
              and r_r8.get("expired_due_box") is True
              and abs(pg._state()["factor"] - 1.90) < 1e-9,
              (r_r8, _exp_r8, _br8, pg._state().get("factor")))
        #    R9 (Copilot's second [high] on 39079b5): the DUE-AT-SAVE
        #    case — the tick landed (rot 0) but the heartbeat's expiry has
        #    NOT run when the guard looks. Keeping cur's rot-0 box under
        #    the setter's new factor hands the in-flight expiry a drift
        #    (after != factor) and loses the anchor; the guard must
        #    perform the expiry ITSELF, inline: ledgered at the anchor's
        #    truth (reverted / after == the anchor) and the new box births
        #    from that anchor — a rot-0 zombie under a new factor is the
        #    spent guarantee re-arming itself by accident.
        pg._save({"factor": 2.0, "auto_adjust_box": None})
        aa.apply_adjustment(-0.05, "RA-R", "ev:ra-r")
        aa.tick_rotation()
        aa.tick_rotation()                              # 3 -> 1
        _real_save_r9 = pg._save
        _armed9 = {"on": True}
        def _save9(*a, **k):
            if _armed9["on"]:
                _armed9["on"] = False
                aa.tick_rotation()      # 1 -> 0: due AT the save, the
                                        # expiry NOT yet run
            return _real_save_r9(*a, **k)
        pg._save = _save9
        try:
            r_r9 = aa.set_guard_factor_temporarily(1.90, 2, before=1.95)
        finally:
            pg._save = _real_save_r9
        _exp_r9 = [r for r in pg._state()["auto_adjust_ledger"]
                   if str(r.get("reason", "")).startswith("time_box_expired")
                   and r.get("rotation_id") == "RA-R"]
        _br9 = pg._state().get("auto_adjust_box") or {}
        check("re-arm: a box due AT THE SAVE expires in the guard — "
              "ledgered at the anchor's truth and birthed from it, never "
              "kept alive as a rot-0 zombie under the new factor",
              len(_exp_r9) == 1 and _exp_r9[0]["status"] == "reverted"
              and _exp_r9[0]["after"] == 2.0
              and _br9.get("rotations_left") == 2
              and _br9.get("before") == 2.0
              and r_r9.get("expired_due_box") is True
              and abs(pg._state()["factor"] - 1.90) < 1e-9,
              (r_r9, _exp_r9, _br9, pg._state().get("factor")))
    finally:
        aa.DAILY_CAP = _ra_cap

finally:
    if _real_state_file is not None:
        pg.STATE_FILE = _real_state_file
    else:
        del pg.STATE_FILE    # the pin was never there — the default resumes
    shutil.rmtree(_sbx, ignore_errors=True)

fails = [r for r in results if not r[1]]
rep = BASE / "artifacts" / "auto_adjust" / "_autoadjust_drive.txt"
rep.parent.mkdir(parents=True, exist_ok=True)
rep.write_text("\n".join(f"{'PASS' if ok else 'FAIL'}  {n}" +
                         (f"  [{d}]" if d and not ok else "")
                         for n, ok, d in results)
               + f"\n\nRESULT: {len(results) - len(fails)} passed, "
                 f"{len(fails)} failed\n", encoding="utf-8")
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
