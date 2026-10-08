"""THE VOLATILITY-ENGINE SINGLETON GUARD (Oct 8, red-first): the
launcher's retake helper can never boot a second engine pair.

THE RACE: start_detached.ps1 registered volatility_engine with
$lockFile = "" ("no own guard; the launcher verifies by command line")
— the only duplicate defense was launch_stack's Test-Component, a
Get-CimInstance cmdline probe that (a) misses processes on a single
query (the Aug 28 duplicate-scanner cause), (b) sees nothing during
the engine's boot window (imports + the first full 12-week pull take
seconds), and (c) is also the only defense Launch Kraken.bat has.
Two racing spawn paths + a boot gap = two full OHLCV pullers hammering
the public tape and two writers on rotation_thresholds.json.

THE CURE mirrors the house pid-lock family (scanner's .scanner.pid,
the bot's bot.lock, hindsight's .hindsight.pid) but goes one stronger:
THE ARBITER IS THE KERNEL. The claim is an msvcrt byte-range lock
(LK_NBLCK) on .volatility.pid — atomic (two racers cannot both hold
byte 0), self-releasing (a killed engine's lock vanishes with the
process: a stale file self-heals on the next boot, no retake race is
even possible), held for the process lifetime. The pid file is
diagnostics; only the lock holder writes its pid; the tasklist python
check only enriches the refusal, and an unverifiable holder still
refuses (fail-closed). release_lock is owner-only (no fd, no unlock).
start_detached deliberately does NOT pre-clear the file (a helper-side
Remove-Item races a fresh claim — TOCTOU): the engine is the single
retaker.

RED-FIRST receipts live in the check labels below.

Prints "RESULT: N passed, M failed".
"""
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import volatility_engine as ve

results = []


def check(name, ok, why=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}"
          + (f"  [{why}]" if why and not ok else ""))


# the drive owns its own lock world: the real .volatility.pid of the
# LIVE engine is never touched (rebind, like price_alert's LOCK_FILE).
SBX = HERE / "scratch"
SBX.mkdir(exist_ok=True)

# ════════ 0b. THE SANDBOX-LEAK GUARD (the worthladder port, user ask
# Oct 8): every write-capable open (builtins + io + fd-level os.open)
# and every os.replace the DRIVE performs is recorded; the tail proves
# none of them touched a live-state path. FOR THIS DRIVE THE GUARD IS
# THE POINT: the whole suite exists so the singleton machinery never
# writes the REAL .volatility.pid / .volatility.lock at the repo root —
# a forgotten rebind (the exact regression class the rebinding exists
# to prevent) would now fail the drive instead of silently docking the
# live engine. Passive by design: the audit never blocks a write, it
# JUDGES the log at the end. The holder SUBPROCESSES are outside this
# process's audit (they carry their own sandboxed paths in their -c
# source, asserted by the checks below); the drive-process writes —
# claim/release, the pid templates, the sweep — are all audited here.
import io as _io
import builtins as _builtins
import tempfile as _tf_warm
_tf_warm.gettempdir()   # warm the lazy tempdir BEFORE the shim (CPython's
                        # first-use probe file is machinery, not a drive write)

def _path_str(p):
    if isinstance(p, bytes):
        return os.fsdecode(p)
    try:
        v = os.fspath(p)
        return os.fsdecode(v) if isinstance(v, bytes) else v
    except TypeError:
        return None

_WRITTEN = []
_REAL_OPEN = _builtins.open

def _audit_open(file, mode="r", *a, **k):
    try:
        _p = _path_str(file)
        if _p is not None and any(_c in (mode or "") for _c in "wax+"):
            _WRITTEN.append(_p)
    except Exception:
        pass
    return _REAL_OPEN(file, mode, *a, **k)

_builtins.open = _audit_open
_io.open = _audit_open          # Path.write_text/bytes resolve through here
_REAL_OSOPEN = os.open

def _audit_osopen(file, flags, *a, **k):
    try:
        _w_flags = (os.O_WRONLY, os.O_RDWR, os.O_APPEND,
                    os.O_CREAT, os.O_TRUNC)
        if any(flags & f for f in _w_flags):
            _p = _path_str(file)
            if _p is None and not isinstance(file, int):
                _p = f"<unattributable:{file!r}>"  # fail-closed: a leak
            if k.get("dir_fd") is not None and _p is not None:
                _p = f"<dirfd-relative:{_p}>"      # fail-closed: a leak
            if _p is not None:
                _WRITTEN.append(_p)
    except Exception:
        pass
    return _REAL_OSOPEN(file, flags, *a, **k)

os.open = _audit_osopen
_REAL_REPLACE = os.replace

def _audit_replace(src, dst, *a, **k):
    try:
        _p = _path_str(dst)
        if _p is not None:
            _WRITTEN.append(_p)
    except Exception:
        pass
    return _REAL_REPLACE(src, dst, *a, **k)

os.replace = _audit_replace
REAL_LOCK = ve.VOL_PID_FILE
ve.VOL_PID_FILE = str(SBX / "_vol_singleton.pid")
ve.VOL_LOCK_FILE = str(SBX / "_vol_singleton.lock")

# HERMETIC WORLD: a previous drive run's orphaned holder (a bare
# shim-kill leaves the real python child alive, still holding the
# kernel lock) would refuse every claim in this run — the drive owns
# its sandbox, so it sweeps its own strays before judging anything.
subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
     "Where-Object { $_.CommandLine -match '_vol_singleton' } | "
     "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
    capture_output=True, timeout=60)
for _f in ("_vol_singleton.pid", "_vol_singleton.lock"):
    try:
        (SBX / _f).unlink()
    except FileNotFoundError:
        pass
time.sleep(0.5)


def _lock_path() -> Path:
    return Path(ve.VOL_PID_FILE)


def _lock_pid() -> int:
    try:
        return int(_lock_path().read_text(encoding="utf-8").strip() or 0)
    except Exception:
        return 0


def _holder_src(hold_s: int = 60) -> str:
    """A subprocess that claims the sandbox lock and HOLDS it (the
    kernel lock lives in that process). BOTH files rebind: the pid
    file AND the kernel-lock arbiter — a holder left on the real
    .volatility.lock would fight the live engine's own guard."""
    return ("import sys, time; sys.path.insert(0, r'%s'); " % HERE
            + "import volatility_engine as ve; "
            + "ve.VOL_PID_FILE = r'%s'; " % _lock_path()
            + "ve.VOL_LOCK_FILE = r'%s'; " % str(Path(ve.VOL_LOCK_FILE))
            + "ok = ve.claim_lock(); "
            + "print('HELD' if ok else 'REFUSED', flush=True); "
            + "time.sleep(%d)" % hold_s)


def _spawn_holder(hold_s: int = 60):
    p = subprocess.Popen([sys.executable, "-X", "utf8", "-c",
                          _holder_src(hold_s)],
                         stdout=subprocess.PIPE, text=True)
    verdict = p.stdout.readline().strip()   # blocks until claimed/refused
    return p, verdict


def _tree_kill(p):
    """Kill the WHOLE pair: sys.executable is the venv shim whose real
    python child is the actual holder — a bare kill() orphans the child
    with the kernel lock still held (the production shape is stop_
    stack's two-pass sweep; the drive uses taskkill /T)."""
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                   capture_output=True, timeout=30)


# ════════ 1. the lock lives and follows the engine's world ════════
check("1.1 the engine names a pid lock file (VOL_PID_FILE beside the "
      "module by default, rebindable for drives)",
      bool(REAL_LOCK) and REAL_LOCK.endswith(".volatility.pid"),
      f"VOL_PID_FILE={REAL_LOCK!r}")

print("== 2. RED-FIRST: the guard functions exist and behave ==")

# ── 2.1 a LIVE holder is refused (real subprocess holding the lock) ──
_h1, _v = _spawn_holder()
try:
    check("2.1 a LIVE holder REFUSES the second claim (a real "
          "subprocess holds the kernel lock)",
          _v == "HELD" and ve.claim_lock() is False,
          f"holder verdict={_v!r}, second claim not refused")
finally:
    _tree_kill(_h1)
time.sleep(0.5)   # the kernel releases the dead holder's lock

# ── 2.2 the STALE world self-heals: after the holder's death the ─────
# claim succeeds and the pid file is re-stamped (no retake race — the
# kernel released the dead lock with the process)
_rc2 = ve.claim_lock()
check("2.2 a dead holder's lock SELF-HEALS — the next claim wins and "
      "re-stamps the pid file (the kernel released the lock with the "
      "process)",
      _rc2 is True and _lock_pid() == os.getpid(),
      f"claim={_rc2}, lock_pid={_lock_pid()}, ours={os.getpid()}")

# ── 2.3 the recycled-pid trap: a live NON-python pid never blocks ────
check("2.3 a live NON-python pid (recycled-pid trap) does not block — "
      "the tasklist check demands 'python'",
      ve._pid_alive_python(4) is False,   # pid 4 = Windows System, never python
      "_pid_alive_python(4) returned True")

# ── 2.4 tasklist FAILURE is fail-closed (the review major) ───────────
_real_run = ve.subprocess.run
def _exploding_run(*a, **k):
    raise RuntimeError("tasklist drill: the check itself failed")
ve.subprocess.run = _exploding_run
try:
    # an unverifiable holder must NEVER read as dead: the probe refuses
    check("2.4 an unverifiable holder (tasklist fails) is NEVER treated "
          "as dead — the probe returns None, not False",
          ve._pid_alive_python(4) is None,
          "broken tasklist masqueraded as a verdict")
finally:
    ve.subprocess.run = _real_run

# ── 2.5 release: the owner's release removes the lock; double-safe ───
ve.release_lock()
check("2.5 the owner's release_lock removes the file",
      not _lock_path().exists())
ve.release_lock()
check("2.6 a second release is a no-op (no fd, no crash on the "
      "shutdown race)", True)

# ── 2.7 THE ATOMIC CLAIM (the review major): two racing claimers ─────
# across REAL processes — the kernel byte-range lock lets exactly one
# hold byte 0. Both hold briefly so the loser re-judges against a live
# holder, not a corpse.
_r1 = subprocess.Popen([sys.executable, "-X", "utf8", "-c",
                        _holder_src(4)], stdout=subprocess.PIPE, text=True)
_r2 = subprocess.Popen([sys.executable, "-X", "utf8", "-c",
                        _holder_src(4)], stdout=subprocess.PIPE, text=True)
_v1 = _r1.stdout.readline().strip()
_v2 = _r2.stdout.readline().strip()
_tree_kill(_r1)
_tree_kill(_r2)
_wins = (_v1 == "HELD") + (_v2 == "HELD")
check("2.7 two RACING claimers: exactly ONE holds the pair (the "
      "kernel byte-range lock cannot be double-held)",
      _wins == 1, f"racer1={_v1!r}, racer2={_v2!r}")

# ── 2.8 a held lock + broken tasklist still refuses (fail-closed) ────
_h2, _v = _spawn_holder()
ve.subprocess.run = _exploding_run
try:
    check("2.8 a held lock with an unverifiable holder REFUSES (never "
          "a silent pass)",
          _v == "HELD" and ve.claim_lock() is False,
          f"holder={_v!r}, claim not refused with the check broken")
finally:
    ve.subprocess.run = _real_run
    _tree_kill(_h2)
time.sleep(0.5)

# ── 2.9 release is OWNER-ONLY (the review medium): a non-owner's ─────
# release never deletes the holder's lock
_h3, _v = _spawn_holder()
try:
    ve.release_lock()
    _still = _lock_path().exists() and _lock_pid() not in (0, os.getpid())
    check("2.9 a NON-owner's release_lock leaves the holder's lock "
          "intact (the holder's pid stays stamped in the readable pid "
          "file)",
          _v == "HELD" and _still,
          f"holder={_v!r}, lock_pid={_lock_pid()}, ours={os.getpid()}")
finally:
    _tree_kill(_h3)
time.sleep(0.5)
ve.claim_lock()
ve.release_lock()   # clean the sandbox world

# ════════ 3. the wiring: guard runs BEFORE boot work ════════
import inspect
_src_main = inspect.getsource(ve.main)
_src_bottom = (HERE / "volatility_engine.py").read_text(encoding="utf-8",
                                                        errors="replace")
_first = _src_main.find("claim_lock")
_banner = _src_main.find('"=" * 60')   # the boot banner block's first line
check("3.1 main() claims the lock (source pin: claim_lock appears in "
      "main)", _first != -1)
check("3.2 the claim comes BEFORE any boot work (first statement "
      "region of main, before the banner)",
      _first != -1 and _banner != -1 and _first < _banner,
      f"claim at {_first}, banner at {_banner}")
check("3.3 the refusal exits LOUD (SystemExit on a refused claim — "
      "the launcher's early-death capture names the cause)",
      "SystemExit" in _src_main or "sys.exit" in _src_main,
      "no exit on refusal found in main()")
check("3.4 the loop's exit paths release the lock (release_lock in "
      "main)", "release_lock" in _src_main,
      "main never releases the lock")

# ════════ 4. the launcher does not race the guard ════════
_ps = (HERE / "start_detached.ps1").read_text(encoding="utf-8",
                                              errors="replace")
_vol_branch = _ps.split('"volatility_engine" {')[1].split("}")[0] \
    if '"volatility_engine" {' in _ps else ""
check("4.1 start_detached does NOT retake the volatility lock (the "
      "engine's kernel-lock guard is the single retaker — a "
      "helper-side Remove-Item would race a fresh claim, TOCTOU)",
      "$lockFile" not in _vol_branch.replace('$lockFile = ""', ""),
      _vol_branch.strip()[:160])
check("4.2 the old 'no own guard' comment is gone (the contract "
      "moved into the engine)",
      "no own guard; the launcher verifies by command line"
      not in _ps,
      "the stale comment survives")

# ════════ THE SANDBOX-LEAK VERDICT (the guard's tail, the worthladder
# port): the drive's write log is judged — every write must have landed
# in the scratch sandbox (SBX — this drive's lock world IS scratch, by
# design) or the system tempdir. ONE leak is a FAIL — and for this
# drive the named leak would be the suite's own nightmare: the REAL
# .volatility.pid / .volatility.lock at the repo root, written by a
# forgotten rebind. Containment is judged on RESOLVED paths (realpath,
# not string prefixes), and an unresolvable path is a leak
# (fail-closed).
_sbx_r = os.path.realpath(str(SBX))
_tmp_r = os.path.realpath(__import__("tempfile").gettempdir())

def _contained(w):
    try:
        if w.startswith("<"):  # unattributable/dirfd markers: fail-closed
            return False
        r = os.path.realpath(str(w))
        return any(os.path.commonpath([r, root]) == root
                   for root in (_sbx_r, _tmp_r))
    except Exception:
        return False

_leaks = [w for w in _WRITTEN if not _contained(w)]
check("SANDBOX-LEAK the drive wrote ZERO live-state paths (every write "
      "landed in the scratch sandbox or a tempdir — the REAL "
      ".volatility.pid/.lock at the repo root was never touched)",
      not _leaks,
      f"{len(_WRITTEN)} writes audited; leaks: {sorted(set(_leaks))[:5]}")
check("SANDBOX-LEAK the audit is not vacuous (the drive really did "
      "write through the guard, into the sandbox)",
      len(_WRITTEN) > 0
      and any(_contained(w) and os.path.commonpath(
          [os.path.realpath(str(w)), _sbx_r]) == _sbx_r for w in _WRITTEN),
      f"writes recorded: {len(_WRITTEN)}")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
