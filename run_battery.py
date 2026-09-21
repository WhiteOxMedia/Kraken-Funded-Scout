"""run_battery.py - THE ONE-COMMAND BATTERY (rebuilt Sep 15 after the
deletion event took the original with it).

Every verification the project owns, one command:
  - every test_*.py in the scout root (auto-discovered, so a new suite
    is an entry the moment it exists - the flood suite became #104 that
    way on Sep 14);
  - the DIAG_RUN subset of diag_* worlds (the structural gates + hermetic
    replays - fast, deterministic, sandbox-safe);
  - every other diag_* is listed as SKIP so the master list stays
    visible and nothing silently disappears from discovery.

Output: a streaming per-entry line (so a hang is visible live), then a
summary table with pass-counts parsed from each suite's own final
"N/M" line. Exit 0 only when every RUN entry passed.

Run:  .venv\\Scripts\\python.exe -X utf8 run_battery.py
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

SCOUT = Path(__file__).resolve().parent
PY = SCOUT / ".venv" / "Scripts" / "python.exe"
if not PY.exists():
    PY = Path(sys.executable)

# The diag worlds the battery runs (kept fast + hermetic by design).
# Heavy/interactive replays (fleet studies, live-API calibrations, the
# sim-arc seeders) stay manual: listed as SKIP below.
DIAG_RUN = {
    "diag_hygiene",        # root-litter gate (both repos)
    "diag_cachesweep",     # cached-function staleness hazards
    "diag_stshadow",       # st-shadowing (swallowed widgets)
    "diag_widgetsweep",    # stateless widget sweep
    "diag_dialaudit",      # shared-file dial contracts (volume/watch/exit)
    "diag_silentpass",     # except-pass gates, fleet-wide
    "diag_prefsmirror",    # prefs JSON mirror integrity
    "diag_sizingaudit",    # read-before-assign in sizing gates
    "diag_smoketier",      # THE $1K-TIER SMOKE: guard/carry/stop by arithmetic
    "diag_testleaks",      # module-global leaks in the suites
}

PER_ENTRY_TIMEOUT = 420   # seconds; a hung suite fails instead of hanging

# Per-suite budget overrides. test_apptest is the heavy plate suite (every
# MASTER render, 400+ pins) and its runtime grows with every plate added -
# it has tripped the flat 420s watchdog before, and its growth has only one
# direction. The override hands it 900s while every other entry keeps the
# tight 420s hang-detection budget: a genuinely hung suite is still declared
# dead fast, and the heavy suite's extra headroom is stated here instead of
# hiding in a global bump. The WARN_FRACTION line in the summary makes any
# suite trending toward its budget visible BEFORE the trip.
TIMEOUT_OVERRIDES = {
    "test_apptest": 900,
}
WARN_FRACTION = 0.5       # PASS using >50% of budget gets a WARN in its detail


def budget_for(stem: str) -> int:
    """The watchdog budget for one entry (override map, else the default)."""
    return TIMEOUT_OVERRIDES.get(stem, PER_ENTRY_TIMEOUT)


PASS_RE = re.compile(r"(\d+)/(\d+)\s*$")

# THE PRODUCTION-LEDGER GUARD (Sep 17): the battery's contest-window
# suite wiped the LIVE watch mid-window (INJ/ATOM erased at 13:54 while
# their windows ran) - the suite passed its own checks while erasing
# production. The guard snapshots the two ledgers no suite may touch,
# and a destructive change fails the entry even if its checks passed.
# A live fire may legitimately ENRICH the watch mid-battery; only the
# erase signature (entries -> {}) or a contest_id move is a leak.
PROD_GUARD = ("contest_watch.json", "contest_account.json")


def _guard_snap() -> dict:
    snap = {}
    for name in PROD_GUARD:
        p = SCOUT / name
        try:
            snap[name] = p.read_text(encoding="utf-8") if p.exists() else None
        except OSError:
            snap[name] = "<unreadable>"
    return snap


def _guard_live_entries(watch_json: str | None) -> int:
    """How many watch entries held an OPEN window at parse time (added
    + window_min still in the future). Unparseable fields count as
    live — the guard fails toward flagging. A wipe that erases only
    EXPIRED entries is the scanner's own quiet retirement (ATOM's
    16:15 pass) and must not flag; erasing a live window is the
    13:54-class leak."""
    try:
        w = json.loads(watch_json) if watch_json else None
    except (ValueError, TypeError):
        return -1                      # unreadable — let the caller flag
    if not isinstance(w, dict):
        return 0
    from datetime import datetime
    live = 0
    for pair, e in w.items():
        try:
            added = datetime.fromisoformat(str((e or {}).get("added")))
            win = float((e or {}).get("window_min") or 0.0)
            exp = added.timestamp() + win * 60.0
            if exp >= time.time():
                live += 1
        except Exception:
            live += 1                  # unknown window — assume live
    return live


def _guard_leak(before: dict, after: dict) -> str | None:
    for name in PROD_GUARD:
        b, a = before.get(name), after.get(name)
        if b == a:
            continue
        if name == "contest_watch.json":
            try:
                bj = json.loads(b) if b else None
                aj = json.loads(a) if a else None
            except (ValueError, TypeError):
                return f"{name} became unreadable"
            if b is None and a is not None:
                return f"{name} was CREATED during the battery"
            if b is not None and a is None:
                return f"{name} was DELETED during the battery"
            if (bj or {}) and isinstance(aj, dict) and not aj:
                # window-aware (Sep 17, the ATOM retirement lesson): a
                # wipe is a leak only when a STILL-LIVE window died
                if _guard_live_entries(b) > 0:
                    return f"{name} was WIPED to empty during the battery"
            continue  # enrichment (a live fire adding an entry) is fine
        # contest_account.json: only the window stamp matters (a balance
        # write from a live fill can land mid-battery; a WINDOW move is
        # the phantom-reset signature and never legitimate here)
        try:
            bid = str((json.loads(b) or {}).get("contest_id") or "")
            aid = str((json.loads(a) or {}).get("contest_id") or "")
        except (ValueError, TypeError):
            return f"{name} became unreadable"
        if bid != aid:
            return (f"{name} contest_id moved "
                    f"{bid[:19]} -> {aid[:19]}")
    return None


def run_entry(path: Path):
    """Returns (status, seconds, detail). status in PASS/FAIL/TIMEOUT."""
    t0 = time.monotonic()
    try:
        p = subprocess.run(
            [str(PY), "-X", "utf8", str(path)],
            cwd=str(SCOUT), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=budget_for(path.stem))
    except subprocess.TimeoutExpired:
        return "TIMEOUT", time.monotonic() - t0, f">{budget_for(path.stem)}s"
    dt = time.monotonic() - t0
    if p.returncode != 0:
        tail = (p.stdout or "").strip().splitlines()[-3:]
        err = (p.stderr or "").strip().splitlines()[-2:]
        detail = " | ".join(tail + err)[:160]
        return "FAIL", dt, detail
    m = None
    for line in (p.stdout or "").splitlines():
        m2 = PASS_RE.search(line.strip())
        if m2:
            m = m2
    det = f"{m.group(1)}/{m.group(2)}" if m else ""
    b = budget_for(path.stem)
    if dt > b * WARN_FRACTION:
        det = (det + f"  WARN: {dt:.0f}s of {b}s budget").strip()
    return "PASS", dt, det


def main(argv: list[str] | None = None) -> int:
    # THE CHUNK FILTER (Sep 20): an optional substring arg runs only the
    # matching entries (`run_battery.py _batt_chunk.log '' trend"). The
    # battery's growth (100+ entries, the plate suite alone 900s) means
    # a full run no longer fits a single shell window - chunks let a
    # session sweep the family in slices and still read one summary.
    _argv = list(argv if argv is not None else sys.argv[1:])
    _flt = _argv[2] if len(_argv) > 2 else ""
    tests = sorted(SCOUT.glob("test_*.py"))
    diags = sorted(SCOUT.glob("diag_*.py"))
    if _flt:
        tests = [t for t in tests if _flt in t.stem]
        diags = [d for d in diags if _flt in d.stem]
    rows = []          # (kind, name, status, seconds, detail)
    print(f"KRAKEN BATTERY - {len(tests)} suites, "
          f"{len(diags)} diags ({len(DIAG_RUN & {d.stem for d in diags})} run)"
          + (f"  [chunk: '{_flt}']" if _flt else ""))
    for i, t in enumerate(tests, 1):
        _gs = _guard_snap()
        st, dt, det = run_entry(t)
        _leak = _guard_leak(_gs, _guard_snap())
        if _leak:
            st, det = "FAIL", f"LEDGER LEAK: {_leak}"
        rows.append(("test", t.stem, st, dt, det))
        mark = {"PASS": ".", "FAIL": "F", "TIMEOUT": "T"}[st]
        print(f"  [{i:3d}] {mark} {t.stem:<34} {dt:6.1f}s  {det}",
              flush=True)
    for d in diags:
        if d.stem in DIAG_RUN:
            _gs = _guard_snap()
            st, dt, det = run_entry(d)
            _leak = _guard_leak(_gs, _guard_snap())
            if _leak:
                st, det = "FAIL", f"LEDGER LEAK: {_leak}"
            rows.append(("diag", d.stem, st, dt, det))
            print(f"  [diag] {'.' if st == 'PASS' else 'X'} {d.stem:<34} "
                  f"{dt:6.1f}s  {det}", flush=True)
        else:
            rows.append(("diag", d.stem, "SKIP", 0.0, "manual world"))

    print("\n" + "=" * 72)
    print(f"{'ENTRY':<38}{'RESULT':<10}{'SECS':>7}  DETAIL")
    print("-" * 72)
    n_pass = n_fail = n_skip = 0
    for kind, name, st, dt, det in rows:
        print(f"{name:<38}{st:<10}{dt:>7.1f}  {det}")
        if st == "PASS":
            n_pass += 1
        elif st == "SKIP":
            n_skip += 1
        else:
            n_fail += 1
    print("-" * 72)
    print(f"BATTERY: {n_pass} passed, {n_fail} FAILED, {n_skip} skipped "
          f"(manual diag worlds)")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())