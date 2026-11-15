"""THE SCANNER SINGLETON, REBUILT ON A KERNEL LOCK (Nov 15).

The scanner's guard used to be an O_EXCL pid file plus a tasklist probe. It
refused duplicates in practice, but it had the shape the volatility engine's
guard was rebuilt away from: the file can be unlinked between the "stale?" read
and the create, so two racing starts can both believe they won, and a recycled
pid needs a takeover path that is itself a race.

scanner_singleton claims an msvcrt byte-range lock instead. The kernel decides:
two racers cannot both hold byte 0, a killed scanner's lock dies with its
process (so a stale pid file self-heals with no takeover race), and only the
holder writes the pid file.

Every check runs against SANDBOX paths — the live .scanner.pid / .scanner.lock
are read-only witnesses here (and the tail asserts they were not touched).

Prints "RESULT: N passed, M failed".
"""
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import scanner_singleton as ss

results = []


def check(name, ok, why=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}"
          + (f"  [{why}]" if why and not ok else ""))


SBX = HERE / "scratch"
SBX.mkdir(exist_ok=True)
PID = SBX / "_scanner_singleton.pid"
LOCK = SBX / "_scanner_singleton.lock"

# the live pair is observed, never written
LIVE_PID = Path(ss.SCANNER_PID_FILE)
_live_before = LIVE_PID.read_text(encoding="utf-8").strip() if LIVE_PID.exists() else None
_live_lock_before = Path(ss.SCANNER_LOCK_FILE).exists()

for _f in (PID, LOCK):
    try:
        _f.unlink()
    except FileNotFoundError:
        pass

ss.SCANNER_PID_FILE = str(PID)
ss.SCANNER_LOCK_FILE = str(LOCK)


def _holder_src(hold_s: int = 30) -> str:
    return ("import sys, time; sys.path.insert(0, r'%s'); "
            "import scanner_singleton as ss; "
            "ss.SCANNER_PID_FILE = r'%s'; ss.SCANNER_LOCK_FILE = r'%s'; "
            "print('HELD' if ss.claim_lock() else 'REFUSED', flush=True); "
            "time.sleep(%d)" % (HERE, PID, LOCK, hold_s))


def _spawn_holder(hold_s: int = 30):
    p = subprocess.Popen([sys.executable, "-X", "utf8", "-c", _holder_src(hold_s)],
                         stdout=subprocess.PIPE, text=True)
    verdict = p.stdout.readline().strip()
    return p, verdict


def _tree_kill(p):
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                   capture_output=True, timeout=30)


print("== 1. the claim itself ==")
check("1.1 the guard names a lock arbiter and a pid diagnostic beside the module",
      str(ss.SCANNER_LOCK_FILE).endswith("_scanner_singleton.lock")
      and str(ss.SCANNER_PID_FILE).endswith("_scanner_singleton.pid"),
      (ss.SCANNER_LOCK_FILE, ss.SCANNER_PID_FILE))

_ok = ss.claim_lock()
check("1.2 a free pair is claimed and the pid is stamped",
      _ok and PID.read_text(encoding="utf-8").strip() == str(os.getpid()),
      (PID.read_text(encoding="utf-8").strip() if PID.exists() else None,
       os.getpid()))
check("1.3 the claim created the lock file", LOCK.exists())
ss.release_lock()
check("1.4 the owner's release removes BOTH files",
      not PID.exists() and not LOCK.exists())

print("== 2. a live holder is refused; a dead one self-heals ==")
_h1, _v = _spawn_holder()
try:
    check("2.1 a LIVE holder refuses the second claim", _v == "HELD" and not ss.claim_lock(),
          f"holder={_v!r}")
    check("2.2 a refused racer does not touch the holder's pid record",
          PID.read_text(encoding="utf-8").strip() == str(_h1.pid - 0).strip()
          or PID.read_text(encoding="utf-8").strip() not in ("", str(os.getpid())),
          PID.read_text(encoding="utf-8").strip() if PID.exists() else None)
finally:
    _tree_kill(_h1)
time.sleep(0.6)

_ok = ss.claim_lock()
check("2.3 after the holder dies the lock SELF-HEALS (no takeover code) — "
      "the next claim wins and re-stamps",
      _ok and PID.read_text(encoding="utf-8").strip() == str(os.getpid()),
      (_ok, PID.read_text(encoding="utf-8").strip() if PID.exists() else None))
ss.release_lock()

print("== 3. two RACERS: exactly one wins ==")
_r1 = subprocess.Popen([sys.executable, "-X", "utf8", "-c", _holder_src(4)],
                       stdout=subprocess.PIPE, text=True)
_r2 = subprocess.Popen([sys.executable, "-X", "utf8", "-c", _holder_src(4)],
                       stdout=subprocess.PIPE, text=True)
_v1 = _r1.stdout.readline().strip()
_v2 = _r2.stdout.readline().strip()
_tree_kill(_r1)
_tree_kill(_r2)
time.sleep(0.6)
check("3.1 two racing claimers: exactly ONE holds the pair",
      (_v1 == "HELD") + (_v2 == "HELD") == 1, f"racer1={_v1!r} racer2={_v2!r}")

print("== 4. a stale pid file is not the arbiter any more ==")
PID.write_text("999999", encoding="utf-8")     # a dead pid, no lock holder
try:
    LOCK.unlink()
except FileNotFoundError:
    pass
_ok = ss.claim_lock()
check("4.1 a stale pid file does not block the claim (the kernel lock decides)",
      _ok and PID.read_text(encoding="utf-8").strip() == str(os.getpid()),
      (_ok, PID.read_text(encoding="utf-8").strip() if PID.exists() else None))
ss.release_lock()

print("== 5. fail-closed ==")
_real_msvcrt = ss.msvcrt
ss.msvcrt = None
try:
    check("5.1 no msvcrt on the platform refuses the start (never unguarded)",
          ss.claim_lock() is False)
finally:
    ss.msvcrt = _real_msvcrt

_real_pid_path = ss.SCANNER_PID_FILE
ss.SCANNER_PID_FILE = str(SBX)          # a directory: the stamp must fail
try:
    check("5.2 an unstampable pid file refuses the start", ss.claim_lock() is False)
finally:
    ss.SCANNER_PID_FILE = _real_pid_path
    try:
        LOCK.unlink()
    except FileNotFoundError:
        pass

print("== 6. the wiring: the scanner claims before it works ==")
_src = (HERE / "background_scanner.py").read_text(encoding="utf-8", errors="replace")
_main_bit = _src.split('if __name__ == "__main__":')[-1]
check("6.1 the scanner imports and claims the singleton guard",
      "import scanner_singleton as _singleton" in _main_bit
      and "_singleton.claim_lock()" in _main_bit)
check("6.2 a refused claim exits instead of running unguarded",
      "claim_lock()" in _main_bit and "sys.exit(0)" in _main_bit)
check("6.3 the claim happens BEFORE main() is entered",
      _main_bit.find("claim_lock()") < _main_bit.find("        main()"),
      (_main_bit.find("claim_lock()"), _main_bit.find("        main()")))
check("6.4 the lock is released on exit (atexit)", "atexit.register" in _main_bit)
check("6.5 the old O_EXCL guard is gone (no second arbiter to disagree with)",
      "os.O_EXCL" not in _main_bit)

print("== 7. the live pair was never touched by this drive ==")
_live_after = LIVE_PID.read_text(encoding="utf-8").strip() if LIVE_PID.exists() else None
check("7.1 the LIVE .scanner.pid still names the same holder",
      _live_after == _live_before, (_live_before, _live_after))
check("7.2 the LIVE .scanner.lock existence is unchanged",
      Path(ss.SCANNER_LOCK_FILE).exists() == _live_lock_before)

print("== 8. the beat proves its own cadence in the log ==")
import logging
import threading
import background_scanner as bs

_records = []


class _Grab(logging.Handler):
    def emit(self, record):
        _records.append(record.getMessage())


_orig_tick = bs._fast_stop_tick
_orig_period = bs.FAST_STOP_TICK_S
_orig_report = bs.FAST_STOP_REPORT_S
_handler = _Grab()
bs.logger.addHandler(_handler)
# outside boot the module logger inherits root's WARNING level, so an INFO beat
# never reaches a handler; the real process sets INFO in its boot setup.
_orig_level = bs.logger.level
bs.logger.setLevel(logging.INFO)
try:
    bs._fast_stop_tick = lambda ex, cfg: ([], {})
    bs.FAST_STOP_TICK_S = 0.05
    bs.FAST_STOP_REPORT_S = 60.0          # the first three ticks always speak
    _stop = threading.Event()
    _th = threading.Thread(target=bs._fast_stop_watchdog, args=(None, None, _stop),
                           daemon=True)
    _th.start()
    time.sleep(0.6)
    _stop.set()
    _th.join(timeout=2.0)
finally:
    bs._fast_stop_tick = _orig_tick
    bs.FAST_STOP_TICK_S = _orig_period
    bs.FAST_STOP_REPORT_S = _orig_report
    bs.logger.removeHandler(_handler)
    bs.logger.setLevel(_orig_level)

_beats = [m for m in _records if "FAST STOP beat" in m]
check("8.1 the live beat reports its cadence into the log", len(_beats) >= 1, _records[:3])
check("8.2 the report carries ticks, interval, tick duration and the priced count",
      bool(_beats) and "apart" in _beats[0] and "last tick" in _beats[0]
      and "pairs priced" in _beats[0], _beats[:1])

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
