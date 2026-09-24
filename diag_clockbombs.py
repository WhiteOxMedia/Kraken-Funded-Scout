"""diag_clockbombs.py — clock-freeze time bombs in the drives.

THE CLASS (the metalayers shadow bomb, Sep 23): a drive pins a frozen
date/hour while the code under test stamps or compares with the REAL
wall clock, so the suite only passes at certain hours/dates — and the
battery stays green while a check silently stops grading.

THE TIDEMEMORY BOMB (found by the +40d sweep, fixed same day): a fixture
stamped with datetime.now() while the decay math received a time.time()
now_ts — two clock sources in one check. In production they agree; under
any clock perturbation the "fresh" payers silently age past the decay
and the check stops grading what it says it grades.

THE LIVE-DATA BOMB (census 4, Sep 24): a drive that READS a runtime
file the RUNNING SCANNER owns (contest_watch.json, contest_trades.jsonl,
popup_suppressions.jsonl, signals.db, ... — the same literal list
test_hooks_ignore_runtime_state enforces for hooks). Such a drive grades
against whatever the live machine happens to have written: positions
close, ledgers roll, trades age out — and the check silently degrades
or starts failing WEEKS later, fixture staleness that only shows up
with time. Every I/O read whose path expression carries a runtime
literal and NO sandbox marker must carry a `clock-audit: ok` sentinel
(within 3 lines above) documenting the excuse:

  LIVE-PROBE  — a read-only probe asserting SHAPE (verdicts in a known
                set, counts >= 0); it tolerates anything the live book
                contains by construction.
  SANDBOXED   — the runtime NAME was pointed at a tmp file before this
                line runs (the marker variable lives on an earlier
                line — the sentinel names it).
  CENSUS-SRC  — the "read" is source text being pinned, never the file.

And the staleness bomb proper: a read chain whose payload is INDEXED
unguarded (rec["key"] instead of rec.get("key")) — a live row without
that key crashes the drive the day production grows a shape it didn't
have. Flagged even when the read carries a sentinel, unless the chain
itself is sandbox-marked.

WHAT THIS DIAG ENFORCES (cheap, every battery run):
  1. MIXED COMPARISON — a frozen literal compared directly against a
     real-clock call in one expression (hard fail).
  2. DUAL-CLOCK STAMPS — a datetime.now() fixture feeding a time.time()
     now_ts in the same file (fail — the tidememory shape).
  3. HARNESS CANARY — a synthetic suite exec'd under a +40d epoch shift
     via _faketime_light.py must FAIL (an unshifted canary cannot); a
     dead sweep fails loudly instead of green-lighting nothing.
  4. LIVE-DATA READS — every live-file I/O read carries a reviewed
     sentinel; unguarded indexing into a live chain is flagged.
  5. SELF-AUDIT — census 4's scanner must flag the dirty canary and
     pass the sentineled one; a census that cannot catch its own class
     fails loudly instead of green-lighting nothing.

Behavioral faketime verification of specific suites (the deep sweep)
stays a manual scratch job: _faketime_light.py <shift> <suite.py>.
"""
import ast
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
FROZEN_RE = re.compile(r"20\d\d-\d\d-\d\d|datetime\(\s*20\d\d\s*,")
CLOCK_EXPR = re.compile(
    r"(datetime\.now\(|date\.today\(|datetime\.today\(|time\.time\(\)"
    r"|\.now\(\)|\.today\(\)|_now_mst\(\))")

# THE SCANNER-OWNED RUNTIME FILES (the same list the hooks census
# enforces — one class definition, no second opinion) + the data-module
# attribute whose constant points at snapshot_outcomes.jsonl (the
# tidebar import hop).
RUNTIME_LITERALS = [
    "contest_watch.json", "contest_account.json", "contest_trades.jsonl",
    "take_payouts.json", "signal_snapshots.jsonl", "snapshot_outcomes.jsonl",
    "signals.db", "ohlcv_cache.db", "rotation_thresholds.json",
    "rotation_lockout.json", "climb_levels.json", "volatility_universe.json",
    "sure_thing_autoweights.json", "coin_floors.json", "discipline_state.json",
    "scanner_state.json", "scanner.lock", "voice_log.jsonl", "scanner.log",
    "console_state.json", "console_prefs.json", "popup_suppressions.jsonl",
    "payout_track.json", "hype_now.txt", "why_rejected.txt", "pending_check.txt",
    "speech_queue", "tide_bar_reckoning_window.jsonl", "scanner_pulse.json",
]
RUNTIME_MODULE_ATTR = "SNAP_OUTCOMES_FILE"
RT_RE = re.compile(
    "(" + "|".join(re.escape(t) for t in RUNTIME_LITERALS)
    + "|" + RUNTIME_MODULE_ATTR + ")")

# a read is LIVE only when its path expression carries NO sandbox
# marker — the guarded-swap pattern (tmp, mkdtemp, the drive's own
# scratch dirs) writes to its own world, staleness can't reach it
SANDBOX_RE = re.compile(
    r"tmp|mkdtemp|sbx|sandbox|scratch|_td\b|TMPDIR|TEMP"
    r"|_test_|_probe|_clockbomb|_sbx|testbak", re.IGNORECASE)

SENTINEL_RE = re.compile(r"clock-audit:\s*ok", re.IGNORECASE)

# I/O-shaped calls only — a mention in a docstring, a check name, or a
# census string is never a read; the AST call filter kills that noise
IO_PLAIN = {"open", "Path"}
IO_ATTR = {"read_text", "read_bytes", "read", "connect", "exists", "stat",
           "glob", "iterdir", "load", "loads", "write_text", "write_bytes"}

findings = []


def _runtime_scan(src_text: str, fname: str) -> list:
    """Census 4 + the index bomb, over one file's source. Returns the
    finding strings (also the self-audit canary's engine)."""
    out = []
    try:
        tree = ast.parse(src_text)
    except SyntaxError:
        return out
    lines = src_text.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            seg = ast.unparse(node)
            if not RT_RE.search(seg):
                continue
            if isinstance(node.func, ast.Attribute):
                is_io = node.func.attr in IO_ATTR
            elif isinstance(node.func, ast.Name):
                is_io = node.func.id in IO_PLAIN
            else:
                is_io = False
            if not is_io:
                continue
            if SANDBOX_RE.search(seg):
                continue
            blk = "\n".join(lines[max(node.lineno - 4, 0):node.lineno])
            if SENTINEL_RE.search(blk) or SENTINEL_RE.search(lines[node.lineno - 1]):
                continue
            out.append(
                f"{fname}:{node.lineno} LIVE-DATA READ: {seg[:90]} "
                f"— no clock-audit sentinel (a runtime file the running "
                f"scanner owns; staleness shows weeks later)")
        if isinstance(node, ast.Subscript):
            seg = ast.unparse(node.value)
            if not RT_RE.search(seg) or SANDBOX_RE.search(seg):
                continue
            if re.search(r"\.\s*get\s*\(", seg):
                continue          # the guarded form
            out.append(
                f"{fname}:{node.lineno} UNGUARDED LIVE-DATA INDEX: "
                f"{ast.unparse(node)[:90]} — a live row without this key "
                f"crashes the drive weeks from now")
    return out


for py in sorted(ROOT.glob("test_*.py")):
    try:
        src = py.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except SyntaxError:
        continue
    lines = src.splitlines()

    # ── 1 + 2: the clock-freeze censuses (unchanged shape) ────────────
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            seg = [node.left, *node.comparators]
            has_frozen = any(FROZEN_RE.search(ast.unparse(o)) for o in seg)
            has_clock = any(CLOCK_EXPR.search(ast.unparse(o)) for o in seg)
            if has_frozen and has_clock:
                near = "\n".join(lines[max(node.lineno - 3, 0):node.lineno])
                if "clock-audit: ok" in near:
                    continue
                findings.append(
                    f"{py.name}:{node.lineno} MIXED COMPARISON: "
                    f"{ast.unparse(node)[:90]}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "now" \
                and "datetime" in ast.unparse(node.func.value):
            for ln_txt in lines[node.lineno - 1:node.lineno + 12]:
                if "now_ts=time.time()" in ln_txt.replace(" ", ""):
                    blk = "\n".join(lines[max(node.lineno - 4, 0):node.lineno])
                    if "clock-audit: ok" in blk:
                        continue
                    findings.append(
                        f"{py.name}:{node.lineno} DUAL-CLOCK STAMP: "
                        f"datetime.now fixture feeding a time.time now_ts")

    # ── 4: the live-data reads ─────────────────────────────────────────
    findings.extend(_runtime_scan(src, py.name))

# ── 3. the harness canary: the sweep must be ALIVE in this environment ──
# The canary asserts its shifted clock is >= 3,400,000s ahead of the
# marker epoch captured (unshifted) at generation — a +40d shift moves
# time.time() by 3,456,000s, an unshifted run by ~0. Reaching it proves
# the harness actually shifts child processes.
canary = ROOT / "_clockbomb_canary.py"
_marker = time.time()
canary.write_text(
    f"import time\n"
    f"ok = time.time() - {_marker:.0f} >= 3_400_000\n"
    "print('RESULT: 1 passed, 0 failed' if ok else 'RESULT: 0 passed, 1 failed')\n"
    "import sys; sys.exit(0 if ok else 1)\n", encoding="utf-8")
try:
    p = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "_faketime_light.py"),
         "+40d", str(canary)],
        capture_output=True, timeout=60)
    if p.returncode != 0 or b"0 passed" in p.stdout:
        findings.append(
            "HARNESS CANARY: the faketime shift did not reach the canary "
            "— clock-sweep evidence is dead in this environment")
finally:
    canary.unlink(missing_ok=True)

# ── 5. the SELF-AUDIT: census 4 must catch its own class ───────────────
# The DIRTY canary (a bare live read, no sentinel) must be flagged; the
# CLEAN canary (same read + sentinel) must pass. If the scanner cannot
# separate them, the class definition drifted and this diag fails
# loudly instead of green-lighting a census that catches nothing.
_dirty = ("import json\n"
          "with open('scanner_pulse.json') as f:\n"
          "    _p = json.load(f)\n"
          "print(_p['cycles'])\n")
_clean = ("import json\n"
          "# clock-audit: ok — LIVE-PROBE (read-only shape assertion)\n"
          "with open('scanner_pulse.json') as f:\n"
          "    _p = json.load(f)\n"
          "print(_p.get('cycles'))\n")
if not _runtime_scan(_dirty, "_dirty.py"):
    findings.append(
        "SELF-AUDIT: the dirty canary (a bare live read) came back "
        "clean — census 4's scanner is dead")
if _runtime_scan(_clean, "_clean.py"):
    findings.append(
        "SELF-AUDIT: the sentineled canary was flagged — the sentinel "
        "contract is broken")

if findings:
    print("CLOCK-FREEZE TIME BOMBS — drives that may only pass at certain hours:")
    for f in findings:
        print(f"  {f}")
    print(f"\nRESULT: 0 passed, {len(findings)} failed")
    sys.exit(1)
print(f"CLOCK BOMBS: none across {len(list(ROOT.glob('test_*.py')))} drives "
      "(mixed comparisons, dual-clock stamps, harness canary, "
      "live-data reads, self-audit)")
print("RESULT: 1 passed, 0 failed")
sys.exit(0)
