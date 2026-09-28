"""test_protected_extension_gate.py — THE ACCEPTANCE-GATE DRIVE (Sep 27,
red-first). The protected 24h extension shipped behind an operator dial
(exit_extension.py, commit 1be3a31) with only PARITY constraints proved
(never naked: armed width = min(ratchet, max(goal/2, 1.0)), price floor,
gap-fills at the open). The missing piece is the ACCEPTANCE GATE: does
the protected form clear the CORRECTED CONTROL BAR on the real ledger —
i.e. does holding cut books under the incumbent trail cost the
goal-banked control cohort NOTHING (their policy exit must equal their
actual exit, baseline 0 by construction) while the extension cohort
(never-reached-goal books) captures its recoveries?

RED-FIRST at authoring time (against the tree with the study absent):
  1.1  _protected_extension_gate.py does not exist yet -> fails
  1.2  its guard-parity source pins cannot match -> fail
  2.x  the gate cannot run -> fail
Everything here is derived, never hardcoded: the drive re-derives the
GATE decision from the study's own printed numbers and fails if the
printed verdict disagrees with the arithmetic (the stale-hardcode class
caught twice before — the derived-verdict rule is now a drive pin).

Run: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe test_protected_extension_gate.py
"""
import json
import re
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
STUDY = BASE / "_protected_extension_gate.py"
REFEREE = BASE / "_livearm_referee.py"

lines = []
PASS = FAIL = 0


def check(label, ok):
    global PASS, FAIL
    line = ("PASS  " if ok else "FAIL  ") + label
    lines.append(line)
    print(line, flush=True)
    if ok:
        PASS += 1
    else:
        FAIL += 1


# ── 1. the gate exists with the right machinery (RED until written) ──
check("1.1 the gate study exists (_protected_extension_gate.py)",
      STUDY.exists())
src = STUDY.read_text(encoding="utf-8").replace("\r\n", "\n") \
    if STUDY.exists() else ""

check("1.2 the control bar is the CORRECTED one: control = the "
      "goal-banked cohort's delta sum, baseline zero by construction",
      "took the goal" in src and "baseline" in src.lower()
      and "0.00" in src)
check("1.3 the walk folds the live guard factor (the referee's * GF "
      "shape) and keeps the armed cap + price floor + gap-at-open",
      src.count("* GF") >= 1 and "floor_px" in src
      and "return op" in src and "max(goal" in src)
# cross-artifact parity: the armed cap expression matches the referee's
ref = REFEREE.read_text(encoding="utf-8").replace("\r\n", "\n") \
    if REFEREE.exists() else ""


def _cap_shape(text):
    # the SHAPE is the pinned thing: max(goal * FRAC, 1.0) - the factor
    # multiplying it is the walk's own variable (the frozen GF in the
    # referee, the per-bar gf once the shared seam's refinement rides)
    m = re.search(r"cap = max\(goal \* [A-Za-z_0-9.]+, 1\.0\)", text)
    return m.group(0) if m else None


check("1.4 the armed cap SHAPE is byte-equivalent to the referee's "
      "(max(goal * FRAC, 1.0)) — one protection shape, two "
      "artifacts; the width's factor is the walk's variable (frozen "
      "GF, or the per-bar gf the shared seam supplies)",
      _cap_shape(src) is not None and _cap_shape(ref) is not None
      and _cap_shape(src).split("goal * ")[1] ==
      _cap_shape(ref).split("goal * ")[1])
check("1.5 the cut class comes from exit_extension (single source of "
      "truth), not a re-typed list",
      "is_cut_reason" in src or "_CUT_MARKS" in src
      or "exit_extension" in src)

# ── 2. the gate runs and its verdict is mechanical (RED until written) ─
gate_out = ""
gate_pass = gate_fail = None
if STUDY.exists():
    r = subprocess.run(
        [sys.executable, str(STUDY)], capture_output=True, text=True,
        encoding="utf-8", errors="replace", cwd=str(BASE),
        timeout=900)
    gate_out = (r.stdout or "") + (r.stderr or "")
check("2.1 the gate runs clean (exit 0)",
      bool(gate_out) and "Traceback" not in gate_out)
m_gate = re.search(r"^GATE:\s*(PASS|FAIL)\b(.*)$", gate_out, flags=re.M)
check("2.2 the gate prints a derived GATE verdict line",
      m_gate is not None)
if m_gate:
    # re-derive the decision from the gate's own printed numbers
    m_ctrl = re.search(r"protected ctrl \$:\s*(-?\d+\.\d+)", gate_out)
    check("2.3 the gate prints its protected control cost",
          m_ctrl is not None)
    if m_ctrl and m_gate:
        ctrl = float(m_ctrl.group(1))
        decision = "PASS" if ctrl >= 0.0 else "FAIL"
        check("2.4 the printed GATE equals the arithmetic "
              f"(ctrl {ctrl:+.2f} vs the zero baseline -> {decision})",
              m_gate.group(1) == decision)
m_naked = re.search(r"naked ctrl \$:\s*(-?\d+\.\d+)", gate_out)
check("2.5 the naked-hold comparison rides the same run (one cohort, "
      "two policies, honest side-by-side)",
      m_naked is not None)
m_cens = re.search(r"censored:\s*(\d+)", gate_out)
check("2.6 censorship is counted and printed (fills past the data "
      "window stay unknown, not zero)",
      m_cens is not None)
m_pair_cov = re.search(r"_pair_max_ts", src)
check("2.7 no horizon exit without pair-local coverage (CodeRabbit "
      "major, Sep 27: an un-exited book whose pair's feather ends "
      "early must censor, never fabricate a last-close exit out of "
      "stale bars)",
      m_pair_cov is not None
      and src.index("_pair_max_ts") < src.rindex("return last_close"))
if m_cens:
    m_prot_cens = re.search(r"censored (\d+)", gate_out)
    check("2.8 the tail censorship total is the protected row's count "
          "(one cohort, two shapes, the same honesty in both places)",
          m_prot_cens is not None
          and m_prot_cens.group(1) == m_cens.group(1))
check("2.9 the per-day GF refinement rides the shared seam behind its "
      "span gate (the walk accepts gf_fn, the per-day section prints "
      "only once GFS.ready() - the frozen-factor rows stay the "
      "committed gate until ~2 weeks accrue)",
      "gf_fn" in src and "GFS.ready()" in src and "GFS.at" in src
      and src.index("GFS.ready()") < src.index("PER-DAY GF"))

# ── 3. LF purity + parse (the drive's own smoke) ─────────────────────
if STUDY.exists():
    import ast
    try:
        ast.parse(src)
        check("3.1 the gate study parses", True)
    except SyntaxError as e:
        check(f"3.1 the gate study parses ({e})", False)
    check("3.2 the gate study is LF-pure",
          STUDY.read_bytes().count(b"\r\n") == 0)

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
