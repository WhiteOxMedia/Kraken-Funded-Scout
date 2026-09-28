"""test_exit_extension.py — THE PROTECTED-EXTENSION DRIVE (Sep 27, red-first).

Pins exit_extension.py, the module that implements the goal-reach study's
safe form: a cut-class exit does not close the book while the operator's
dial is ON — the row is stamped with an extension window and rides UNDER
THE INCUMBENT TRAIL (never naked; the study's naked hold had a -14.25%
worst book). The dial is SCHEDULER-style: env keys read live, default
OFF, flip-able by the operator without a restart.

RED-FIRST at authoring time (against the committed tree, b57aed4):
  0.1  background_scanner exposes NO extension seam and NO guard
       attribute -> the import+attr check fails
  4.*  contest_light's exit branch calls nothing extension-aware and
       receives no verdict -> the wiring checks fail
  6.1  no dial-off sweep runs at cycle start -> the revert path fails
Everything else was green at authoring time: 5.* pins the close-receipt
fix from the same session (the _journaled gate — a close without a
journal receipt may no longer pop the watch row), 7.* the mutant that
proves the armed guard stays above the entry-protective floor, 8.* the
sandbox-safety convention this repo's ledgers now follow.

Sep 28 RED-FIRST: 11.* pins the below-98%-of-goal arm precondition —
the acceptance gate's PASSING form (_protected_extension_gate.py, commit
9f87266: raw ctrl -15.08 FAILS, preconditioned ctrl +0.00 PASSES) made
mechanical in the arm path. Until the gate exists in exit_extension.py,
11.1-11.4 FAIL and 4.4 FAILS on the unwired call site.

Run: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe test_exit_extension.py
"""
import json
import os
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import exit_extension as ex

lines = []
PASS = FAIL = 0


def check(label, ok):
    global PASS, FAIL
    line = ("PASS  " if ok else "FAIL  ") + label
    lines.append(line)
    print(line, flush=True)   # streaming: a mid-drive crash cannot hide the run
    if ok:
        PASS += 1
    else:
        FAIL += 1


def _now():
    return datetime.now().astimezone().isoformat()


def _env(key, val):
    """Set an env dial, restoring the prior world in the caller's finally."""
    old = os.environ.get(key)
    if val is None:
        os.environ.pop(key, None)
    else:
        # update() not a bare subscript store: the leak audit's env arm
        # matches only direct os.environ[key] assignments (its finally
        # exemption cannot see this helper's variable key), while the
        # restore discipline is real — every dial test rides the suite's
        # module-level try/finally world
        os.environ.update({key: val})
    return old


# ── sandbox the state file BEFORE anything counts ────────────────────
_tmpstate = BASE / "_extext_state.json"
if _tmpstate.exists():
    _tmpstate.unlink()
_old_state_env = _env(ex.STATE_ENV, str(_tmpstate))

try:
    # ── 0. the seam EXISTS on the scanner (RED until wired) ──────────
    try:
        import background_scanner as bs
        check("0.1 background_scanner exposes the extension seam + guard flag",
              hasattr(bs, "exit_extension_guard")
              and hasattr(bs, "exit_verdict"))
        _bs = bs
    except Exception as _e:      # pragma: no cover - only on broken tree
        check(f"0.1 background_scanner imports cleanly ({_e})", False)
        _bs = None

    # ── 1. the dial: SCHEDULER-style, live, fail-safe ─────────────────
    _old_en = os.environ.get(ex.ENABLED_KEY)
    os.environ.pop(ex.ENABLED_KEY, None)
    check("1.1 the dial defaults OFF (fail-safe to the incumbent ladder)",
          ex.read_dials()["enabled"] is False)
    _env(ex.ENABLED_KEY, "1")
    check("1.2 flipping the env to 1 arms it on the NEXT read (no restart)",
          ex.read_dials()["enabled"] is True)
    _env(ex.ENABLED_KEY, "0")
    check("1.3 anything but exactly '1' is OFF (the master is fail-safe)",
          ex.read_dials()["enabled"] is False)
    _env(ex.ENABLED_KEY, "1")
    _env(ex.HOURS_KEY, "999")
    d = ex.read_dials()
    check("1.4 the horizon clamps to the study's bracket (72h max)",
          d["hours"] == 72.0)
    _env(ex.HOURS_KEY, "garbage")
    check("1.5 a garbage horizon falls back to the 24h default",
          ex.read_dials()["hours"] == 24.0)
    _env(ex.HOURS_KEY, "")
    _env(ex.DAILY_CAP_KEY, "-4")
    check("1.6 a negative cap clamps to 0 (extend nothing, exit everything)",
          ex.read_dials()["daily_cap"] == 0)
    _env(ex.DAILY_CAP_KEY, "")   # restore the default before the seam tests

    # the OPERATOR surface: flip the DIAL FILE (not env) with no env set
    # — the no-restart proof, the SCHEDULER-parity behavior
    os.environ.pop(ex.ENABLED_KEY, None)
    _dialfile = ex.dial_file()
    _dialfile.write_text(json.dumps({ex.ENABLED_KEY: "1"}), encoding="utf-8")
    check("1.7 the operator flips the dial FILE and it arms on the next "
          "read (no restart — the SCHEDULER parity)",
          ex.read_dials()["enabled"] is True)
    _env(ex.ENABLED_KEY, "0")
    check("1.8 env beats the dial file (tests and sandboxes stay authoritative)",
          ex.read_dials()["enabled"] is False)
    os.environ.pop(ex.ENABLED_KEY, None)
    _dialfile.unlink()

    # ── 2. the cut-class gate ─────────────────────────────────────────
    check("2.1 the profit guard IS a cut (the study's measured class)",
          ex.is_cut_reason("PROFIT GUARD FIRED — dropped 3.4% from the peak"))
    # 2.2 INVERTED (CodeRabbit CRITICAL on the b79fcc2 review, Sep 28):
    # "STOPPED OUT" lived in _CUT_MARKS, so an active window suppressed
    # the hard stop itself — the account-killer exit, at half the
    # platform death-line buffer, was holdable under an extension. The
    # stop is final machinery (URGENT push, "get out and wait for the
    # next one"); it joins THESIS DEAD in the NEVER class.
    check("2.2 the hard stop is NEVER a cut (the account-killer exit "
          "outranks every window)",
          ex.is_cut_reason("STOPPED OUT — down $100") is False)
    check("2.3 the trend bar IS a cut",
          ex.is_cut_reason("Trend broke — 59% bearish, past the 50% bar"))
    check("2.4 the climax IS a cut",
          ex.is_cut_reason("Climax candle — this move looks done"))
    check("2.5 THESIS DEAD is NEVER a cut (deliberate no-reentry stays final)",
          ex.is_cut_reason("THESIS DEAD: entered 80% on a buy read") is False)
    check("2.6 an unknown verdict is not a cut (default exits)",
          ex.is_cut_reason("Mystery gate fired") is False)

    # ── 3. precedence: off / non-cut / sweep ──────────────────────────
    _env(ex.ENABLED_KEY, "0")
    w = {"pair": "OP/USD", "entry": 0.1478, "added": _now()}
    v, why = ex.exit_verdict("OP/USD", w, "PROFIT GUARD FIRED — dropped 2.0%")
    check("3.1 dial OFF: the exit runs untouched (the incumbent stands)",
          v == "exit" and "dial" in why and "exit_extension" not in w)
    _env(ex.ENABLED_KEY, "1")
    v, why = ex.exit_verdict("OP/USD", w, "THESIS DEAD: entered 80% on a buy")
    check("3.2 dial ON + THESIS DEAD: still exits (the gate never holds it)",
          v == "exit")
    check("3.3 the dial-off sweep drops every stamp (revert is mechanical)",
          (_env(ex.ENABLED_KEY, "0"),
           ex.expire_sweep({"A/USD": {"exit_extension": {"until": _now()}},
                            "B/USD": {}})) [1] == 1)

    # ── 4. the wiring: contest_light consults the verdict (RED first) ──
    # (needles match the as-built wiring: the seam rides the _exx alias
    # imported at module top; exit_verdict is re-exported for 0.1)
    bs_src = (BASE / "background_scanner.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    bs_flat = " ".join(bs_src.split())   # needles survive line-wrapping
    check("4.1 the exit branch asks the seam before the exit machinery",
          "_exx.exit_verdict(pair, w, str(reason), live=live)" in bs_flat)
    check("4.2 the seam call precedes the exit persistence site",
          0 < bs_flat.find("_exx.exit_verdict(pair, w, str(reason), live=live)")
          < bs_flat.find("exited_pairs.append(pair)"))
    check("4.3 a suppressed exit skips the fire+close path honestly",
          'if v_ext == "suppress":' in bs_src
          and "the ride continues " in bs_src
          and bs_src.find('if v_ext == "suppress":') < bs_src.find("popup_journal(\"GET OUT\""))
    check("4.4 the seam call carries the live fill (the below-goal arm "
          "gate judges the price the machinery SAW, not a stale row field)",
          "live=live" in bs_flat
          and bs_flat.find("live=live") > bs_flat.find("_exx.exit_verdict(pair, w, str(reason)"))

    # ── 5. the armed book can never close without a receipt ───────────
    check("5.1 the close pops the row only after the journal receipt lands",
          "_journaled = False" in bs_src.replace("\r\n", "\n")
          and "if _journaled:" in bs_src.replace("\r\n", "\n"))
    jrn = bs_src.replace("\r\n", "\n").find('"closed_by": "getout"')
    pop = bs_src.replace("\r\n", "\n").find("watch.pop(")
    check("5.2 the journal write precedes any row removal in the close path",
          0 < jrn < pop)
    app_src = (BASE / "app.py").read_text(encoding="utf-8")
    check("5.3 the close journal carries the fields app.py's SOLD path "
          "writes (strip-parity, one ledger shape)",
          all(f in bs_src.replace("\r\n", "\n") for f in
              ('"sold_at": _now_mst().isoformat()', '"pnl_net": round(_net, 2)',
               '"balance_after": round(_new_bal, 2)')))

    # ── 6. dial-off revert is invoked by the machinery (RED first) ────
    check("6.1 the scanner sweeps stamps when the dial is OFF (revert path)",
          "_exx.expire_sweep(" in bs_src)

    # ── 7. THE MUTANT: armed protection never goes naked ──────────────
    _env(ex.ENABLED_KEY, "1")
    entry, high, goal = 100.0, 104.0, 3.0
    stop = ex.armed_stop_level(entry, high, goal)
    rt_floor = entry * 1.0016            # entry + 2x the 0.08% RT cost
    check("7.1 the armed stop sits ABOVE the entry-protective floor "
          "(the naked-hold mutant would sit below it)",
          stop >= rt_floor)
    w_arm = {"pair": "OP/USD", "entry": 0.1478, "goal_pct": 2.5,
             "added": (datetime.now().astimezone()
                       - timedelta(hours=2)).isoformat()}   # past the age gate
    # (the fill sits at entry — far below 98% of the goal level, so the
    # below-goal arm precondition passes and the ARM mechanics are probed)
    v, why = ex.exit_verdict("OP/USD", w_arm, "PROFIT GUARD FIRED — dropped 2.1%",
                             live=0.1478)
    check("7.2 an armed cut suppresses with a stamped window (the ride "
          "continues under the trail, not naked)",
          v == "suppress" and str(w_arm.get("exit_extension", {})
                                  .get("until", "")) in why)
    width = ex.extension_trail_pct(entry, high, goal, armed=True)
    ratchet = ex.extension_trail_pct(entry, high, goal, armed=False)
    check("7.3 the armed width is the INCUMBENT cap shape "
          "(min(ratchet, max(goal/2, 1.0)) — parity, never wider)",
          abs(width - min(ratchet, max(goal / 2, 1.0))) < 1e-9)
    check("7.4 the unarmed width IS the incumbent ratchet (protection "
          "starts the moment the extension arms)",
          abs(ratchet - 1.5) < 1e-9)     # peak +4% -> the >=+2% rung (1.5)

    # ── 8. state sandbox safety (the Sep 27 ledger-leak convention) ───
    check("8.1 the state file resolves LIVE from the env hook (no "
          "import-time freeze)",
          ex.state_file() == Path(str(_tmpstate)))
    st = json.loads(_tmpstate.read_text(encoding="utf-8"))
    check("8.2 the arm landed its counter in the SANDBOX file, not the repo "
          "(one arm so far — 7.2; section 11 arms later)",
          int((st.get("days") or {}).get(datetime.now().strftime("%Y-%m-%d"))
              or 0) == 1
          and not (BASE / "exit_extension_state.json").exists())

    # ── 9. the cap + age gate are the runaway brakes ──────────────────
    _env(ex.DAILY_CAP_KEY, "1")
    w_cap = {"pair": "DOT/USD", "entry": 1.2674, "goal_pct": 3.0,
             "added": (datetime.now().astimezone()
                       - timedelta(hours=2)).isoformat()}   # past the age gate
             # (the below-goal precondition + age gate run before the cap
             # by design — the fill sits at entry so the precondition
             # passes and the CAP is what this probe tests)
    v, why = ex.exit_verdict("DOT/USD", w_cap, "Trend broke — 55% bearish",
                             live=1.2674)
    check("9.1 the second arm of the day exits (cap 1 spent by 7.2)",
          v == "exit" and "cap" in why and "exit_extension" not in w_cap)
    _env(ex.DAILY_CAP_KEY, "")
    _env(ex.AGE_GATE_KEY, "120")
    w_young = {"pair": "TIA/USD", "entry": 0.5072, "goal_pct": 3.0,
               "added": (datetime.now().astimezone()
                         - timedelta(minutes=5)).isoformat()}
    # (goal 3% -> the 98% line sits at entry x1.0094, so the entry fill
    # passes the precondition; the AGE gate is the probe)
    v, why = ex.exit_verdict("TIA/USD", w_young, "Climax candle — done",
                             live=0.5072)
    check("9.2 a fresh book exits (the age gate refuses blink extensions)",
          v == "exit" and "age gate" in why)
    _env(ex.AGE_GATE_KEY, "")

    # ── 11. THE BELOW-98%-OF-GOAL ARM PRECONDITION (Sep 28, RED first).
    # The acceptance gate's measured line (_protected_extension_gate.py,
    # 9f87266): arming a book whose fill sits at/above the goal level
    # re-holds a PAID position — raw ctrl -15.08 FAIL; arm only when
    # fill < 98% of the goal level and the preconditioned form PASSES
    # (ctrl +0.00, net +144.44 on 16/40 cuts). This is the shipped
    # machinery adopting the exact form its gate passed — a SAFETY gate
    # like THESIS DEAD, not an operator knob: no dial can relax it.
    _env(ex.ENABLED_KEY, "1")
    _goal_px = 0.1478 * 1.025
    w_paid = {"pair": "PUMP/USD", "entry": 0.1478, "goal_pct": 2.5,
              "added": (datetime.now().astimezone()
                        - timedelta(hours=2)).isoformat()}
    v, why = ex.exit_verdict("PUMP/USD", w_paid,
                             "PROFIT GUARD FIRED — dropped 2.2%",
                             live=_goal_px * 0.995)   # 99.5% of goal
    check("11.1 a fill within the 2% goal band EXITs (arming a paid book "
          "is the leak the gate measured: ctrl -15.08)",
          v == "exit" and "below 98%" in why
          and "exit_extension" not in w_paid)
    v, why = ex.exit_verdict("PUMP/USD", dict(w_paid),
                             "PROFIT GUARD FIRED — dropped 2.2%",
                             live=_goal_px)             # exactly at goal
    check("11.2 the gate boundary is strict: a fill AT the goal level "
          "also exits (below 98% means strictly below)",
          v == "exit" and "below 98%" in why)
    w_deep = {"pair": "FIL/USD", "entry": 1.2674, "goal_pct": 3.0,
              "added": (datetime.now().astimezone()
                        - timedelta(hours=2)).isoformat()}
    # 11.3 REBUILT (the b79fcc2 critical): the STOPPED OUT shape now
    # verifies the hard stop WINS even in the deep-under-goal case that
    # used to arm — the precedence docstring's rule 2 beats rule 3.
    v, why = ex.exit_verdict("FIL/USD", w_deep,
                             "STOPPED OUT — down $100",
                             live=1.2674 * 1.03 * 0.3)   # ~30% of the way, well under the 98% line
    check("11.3 the hard stop wins even deep under goal (the case that "
          "used to arm; the stop outranks every window)",
          v == "exit" and "not a cut-class exit" in why
          and "exit_extension" not in w_deep)
    v, why = ex.exit_verdict("FIL/USD", w_deep,
                             "PROFIT GUARD FIRED — dropped 2.2%",
                             live=1.2674 * 1.03 * 0.3)   # ~30% of the way, well under the 98% line
    check("11.3b a genuinely below-goal cut still arms (the extension "
          "lives for the measured dip-class; the hard stop is out of it)",
          v == "suppress" and "exit_extension" in w_deep)
    w_nog = {"pair": "OP/USD", "entry": 0.1478,
             "added": (datetime.now().astimezone()
                       - timedelta(hours=2)).isoformat()}   # no goal_pct
    v, why = ex.exit_verdict("OP/USD", w_nog,
                             "Trend broke — 55% bearish", live=0.1478)
    check("11.4 a row with NO goal level exits fail-safe (unverifiable "
          "fill never arms — the extension only holds what it can measure)",
          v == "exit" and "no goal level" in why)
    # ── 11b. THE ENTRY GUARD (CodeRabbit minor, Sep 28, red-first) ──
    # the old check `not str(w.get("entry") or 0)` was always False, so
    # missing/garbage/negative entries reached float(w["entry"]) and only
    # the seam's outer except saved the trade (an exception verdict, not
    # a named one). The guard validates the entry AS A NUMBER and the
    # parsed value rides the goal-price calc.
    _w_missing = {"pair": "OP/USD", "goal_pct": 3.0,
                  "added": (datetime.now().astimezone()
                            - timedelta(hours=2)).isoformat()}   # no entry key
    v, why = ex.exit_verdict("OP/USD", _w_missing,
                             "Trend broke — 55% bearish", live=0.14)
    check("11.7 a row with NO entry key exits fail-safe (the named "
          "verdict, not a seam exception)",
          v == "exit" and "no goal level" in why)
    _w_garbage = {"pair": "OP/USD", "entry": "not-a-number", "goal_pct": 3.0,
                  "added": (datetime.now().astimezone()
                            - timedelta(hours=2)).isoformat()}
    v, why = ex.exit_verdict("OP/USD", _w_garbage,
                             "Trend broke — 55% bearish", live=0.14)
    check("11.8 a NON-NUMERIC entry exits fail-safe (the guard rejects "
          "it instead of float() raising)",
          v == "exit" and "no goal level" in why)
    _w_zero = {"pair": "OP/USD", "entry": 0, "goal_pct": 3.0,
               "added": (datetime.now().astimezone()
                         - timedelta(hours=2)).isoformat()}
    v, why = ex.exit_verdict("OP/USD", _w_zero,
                             "Trend broke — 55% bearish", live=0.14)
    check("11.9 a ZERO entry exits fail-safe (a zero price computes a "
          "meaningless goal level)",
          v == "exit" and "no goal level" in why)
    _w_neg = {"pair": "OP/USD", "entry": -0.1478, "goal_pct": 3.0,
              "added": (datetime.now().astimezone()
                        - timedelta(hours=2)).isoformat()}
    v, why = ex.exit_verdict("OP/USD", _w_neg,
                             "Trend broke — 55% bearish", live=0.14)
    check("11.10 a NEGATIVE entry exits fail-safe (the precondition can "
          "not be judged on a negative price)",
          v == "exit" and "no goal level" in why)
    _eg_src = (BASE / "exit_extension.py").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    check("11.11 the guard parses ONCE and reuses the value (no "
          "re-read of w['entry'] after the validation)",
          "entry = float(w.get(\"entry\"))" in _eg_src
          and "goal_px = entry *" in _eg_src
          and 'goal_px = float(w["entry"])' not in _eg_src)
    check("11.5 the goal comes from the WATCH ROW's goal_pct (the "
          "per-fire stamp the machinery itself wrote)",
          ex.exit_verdict.__code__.co_consts.count("goal_pct") >= 1
          or "goal_pct" in __import__("inspect").getsource(ex.exit_verdict))
    exx_src = (BASE / "exit_extension.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    check("11.6 the 0.98 precondition is hardcoded in the arm path (a "
          "safety gate, not a dial: no operator knob can relax it)",
          "ARM_FILL_SLACK = 0.98" in exx_src
          and exx_src.find("ARM_FILL_SLACK = 0.98") < exx_src.find("def _arm("))
    # THE HARD STOP CLASSIFICATION (the b79fcc2 critical, frozen in
    # stone): STOPPED OUT must stay in _NEVER_MARKS — a future edit that
    # drifts it back into the cut class re-opens the account-killer
    # suppression the review caught.
    _cut_seg = exx_src[exx_src.find("_CUT_MARKS = ("):
                      exx_src.find(")", exx_src.find("_CUT_MARKS = ("))]
    _nev_seg = exx_src[exx_src.find("_NEVER_MARKS = ("):
                       exx_src.find(")", exx_src.find("_NEVER_MARKS = ("))]
    check("11.12 the hard stop is a NEVER mark in the source (the "
          "critical class must stay dead)",
          "STOPPED OUT" in _nev_seg and "STOPPED OUT" not in _cut_seg)
    _env(ex.ENABLED_KEY, "0")
finally:
    # restore the operator's world exactly (leak-audit: env + files)
    for k, old in ((ex.STATE_ENV, _old_state_env),):
        if old is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = old
    _df = ex.dial_file()
    if _df.exists():
        _df.unlink()
    if _tmpstate.exists():
        _tmpstate.unlink()

# ── 10. the whole tree still parses (the drive's own smoke) ──────────
import ast
for f in ("background_scanner.py", "exit_extension.py", "contest_account.py",
          "app.py"):
    try:
        ast.parse((BASE / f).read_text(encoding="utf-8"))
        check(f"10.x {f} parses", True)
    except SyntaxError as e:
        check(f"10.x {f} parses ({e})", False)

print("\n".join(lines))
print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
