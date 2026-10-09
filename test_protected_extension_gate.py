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
  1.6+ the naive-stamp rule: the journal join's ms keys normalize
       tz-naive stamps to the house zone (Denver) on BOTH sides, with
       executed teeth (the drive runs the extracted _sold_ms). Sep 28,
       second pass: the burial side's suppression stats were the last
       bare fromisoformat().timestamp() instance (a split-line form
       the original same-line scan could not see) — it now routes
       through _supp_run_ms -> _sold_ms, pin 1.8 grew into a
       function-level AST census (every fromisoformat in either study
       must live inside the seam), and pin 1.11 executes the burial
       side's teeth on cross-era stamps.
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
VS = BASE / "_verdict_study.py"      # the join's other side: ex_ms keys

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

# ── 1b. the naive-stamp rule on the journal join (Sep 28) ────────────
# The join keys _journal_reasons rows by sold_at-ms against the
# grader's ex_ms. A naive stamp (no tz suffix) used to be read in the
# MACHINE'S local zone: identical code on both sides kept the join
# self-consistent, but a winter stamp on a summer machine keyed a
# whole hour off (MDT vs MST), and the rule was never written down.
# House rule (the decay guard's legacy-naive handling, the engine-wide
# 'never hardcode an offset'): naive means DENVER wall time. Both join
# sides normalize the same way, and the drive EXECUTES the rule.
vsrc = VS.read_text(encoding="utf-8").replace("\r\n", "\n") \
    if VS.exists() else ""

def _fromiso_homes(path):
    """Function-level AST census: the enclosing function names of every
    fromisoformat CALL in the file ('<module>' for top-level calls).
    The old same-line text scan missed split-line instances — that is
    exactly how the burial side survived pin 1.8 (Sep 28)."""
    import ast
    if not path.exists():
        return None
    tree = ast.parse(path.read_text(encoding="utf-8"))
    homes = set()

    def visit(node, home):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
                continue
            if isinstance(child, ast.Call) \
                    and getattr(child.func, "attr", "") == "fromisoformat":
                homes.add(home or "<module>")
            visit(child, home)

    visit(tree, None)
    return sorted(homes)


def _module_level_fromiso(path):
    """True when a fromisoformat call executes at FILE TOP LEVEL (the
    studies run their analysis at import; a top-level naive-stamp parse
    is exactly the leak shape the seam must own)."""
    import ast
    if not path.exists():
        return False
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:   # defs hold no module-level execution
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) \
                    and getattr(sub.func, "attr", "") == "fromisoformat":
                return True
    return False


check("1.6 the study's journal key normalizes naive stamps to the "
      "house zone: _sold_ms attaches ZoneInfo America/Denver for "
      "tz-naive rows and _journal_reasons keys through it",
      "_sold_ms" in src and "tzinfo is None" in src
      and 'ZoneInfo("America/Denver")' in src
      and "ms = _sold_ms(" in src)
check("1.7 the grader's ex_ms key normalizes the SAME way (the join "
      "stays comparable by construction — fixing one side alone "
      "would break it for naive rows)",
      "_sold_ms" in vsrc and 'ZoneInfo("America/Denver")' in vsrc
      and "tzinfo is None" in vsrc
      and "ex_ms = _sold_ms(" in vsrc)
check("1.8 the bare fromisoformat().timestamp() idiom is GONE from "
      "BOTH studies — function-level AST census: every fromisoformat "
      "call in either file must live inside the _sold_ms seam (the "
      "old same-line scan missed split-line instances, which is how "
      "the burial side survived it)",
      _fromiso_homes(VS) == ["_sold_ms"]
      and _fromiso_homes(STUDY) == ["_sold_ms"]
      and not _module_level_fromiso(VS) and not _module_level_fromiso(STUDY))


def _exec_fn(path, name):
    """Extract ONE function from a study's AST and exec it - the
    behavioral pin runs the real code without importing the module
    (the studies execute their whole analysis at module level).
    The namespace carries the imports the extracted functions use
    (both datetime spellings + ZoneInfo); the compile filename is a
    placeholder so a traceback cannot mislead by reading the REAL
    file at the segment's mapped line."""
    import ast
    text = path.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            ns = {}
            exec("import datetime\nimport datetime as _dt\n"
                 "from zoneinfo import ZoneInfo", ns)
            exec(compile(ast.get_source_segment(text, node),
                         "<extracted>", "exec"), ns)
            return ns.get(name)
    return None


_f_study = _exec_fn(STUDY, "_sold_ms") if STUDY.exists() else None
_f_vs = _exec_fn(VS, "_sold_ms") if VS.exists() else None
if _f_study is not None:
    from zoneinfo import ZoneInfo
    import datetime as _dt

    def _expect(s):
        t = _dt.datetime.fromisoformat(s)
        if t.tzinfo is None:
            t = t.replace(tzinfo=ZoneInfo("America/Denver"))
        return int(t.timestamp() * 1000)

    _naive_winter = _expect("2026-01-15T10:00:00")   # MST side: UTC-7
    _naive_summer = _expect("2026-07-15T10:00:00")   # MDT side: UTC-6
    _aware = _expect("2026-01-15T10:00:00-06:00")    # aware: untouched
    check("1.9 the naive-stamp rule has TEETH (executed, not just "
          "text): naive winter wall time -> Denver UTC-7, naive "
          "summer -> UTC-6, aware stamps untouched — study side",
          _f_study("2026-01-15T10:00:00") == _naive_winter
          and _f_study("2026-07-15T10:00:00") == _naive_summer
          and _f_study("2026-01-15T10:00:00-06:00") == _aware)
    check("1.10 the grader's key normalizes identically (the join "
          "cannot split between its two sides)",
          _f_vs is not None
          and _f_vs("2026-01-15T10:00:00") == _naive_winter
          and _f_vs("2026-01-15T10:00:00-06:00") == _aware)
    _f_supp = _exec_fn(VS, "_supp_run_ms") if VS.exists() else None
    if _f_supp is not None:
        # the extracted function's namespace is the exec globals - the
        # seam DELEGATES to _sold_ms, so the rule it delegates to must
        # be present there (the extracted _sold_ms itself)
        _f_supp.__globals__["_sold_ms"] = _f_study
    check("1.11 the BURIAL side has the same teeth (Sep 28: the "
          "suppression stats' reach windows were the last bare "
          "fromisoformat().timestamp() instance): _supp_run_ms "
          "normalizes naive stamps to Denver wall time — winter "
          "UTC-7, summer UTC-6, aware untouched — and matches "
          "_sold_ms exactly (one rule, every side)",
          _f_supp is not None and _f_study is not None
          and _f_supp("2026-01-15T10:00:00") == _naive_winter
          and _f_supp("2026-07-15T10:00:00") == _naive_summer
          and _f_supp("2026-01-15T10:00:00-06:00") == _aware
          and _f_supp("2026-01-15T10:00:00")
          == _f_study("2026-01-15T10:00:00"))
else:
    check("1.9 the naive-stamp rule has TEETH (executed, not just "
          "text) — study side", False)
    check("1.10 the grader's key normalizes identically (the join "
          "cannot split between its two sides)", False)
    check("1.11 the BURIAL side has the same teeth (Sep 28: the "
          "suppression stats' reach windows were the last bare "
          "fromisoformat().timestamp() instance)", False)

# ── 2. the gate runs and its verdict is mechanical (RED until written) ─
gate_out = ""
gate_pass = gate_fail = None
gate_rc = None
if STUDY.exists():
    r = subprocess.run(
        [sys.executable, str(STUDY)], capture_output=True, text=True,
        encoding="utf-8", errors="replace", cwd=str(BASE),
        timeout=900)
    gate_out = (r.stdout or "") + (r.stderr or "")
    gate_rc = r.returncode
# 2.1 THE HONEST EXIT PIN (the round-6 minor: the label claimed exit 0
# but only looked for a traceback): the study exits 0 PASS / 2
# FAIL-by-design (the raw dial is SUPPOSED to fail the bar — that IS
# the demonstration), so the check asserts rc matches the printed
# verdict and a crash (traceback, rc 1) fails it — not a blind == 0.
check("2.1 the gate's exit code matches its printed verdict "
      "(0 on PASS, 2 on the by-design FAIL — a crash fails)",
      bool(gate_out) and "Traceback" not in gate_out
      and gate_rc is not None
      and ((gate_rc == 0) if (m21 := re.search(r"^GATE:\s*(PASS|FAIL)\b",
                                               gate_out, flags=re.M))
            and m21.group(1) == "PASS" else
           (gate_rc == 2) if m21 else False))
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


def main():
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    sys.exit(main())
