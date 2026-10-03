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
