"""THE ATOMIC-IO DRIVE (Sep 29): pins the retry contract the production
writers now inherit — named contention retries in-process with a
bounded exponential backoff, everything else raises immediately. The
`replace`/`sleep` injection seams make the math exact: no real waits,
no real files needed except the smoke leg."""
import os
import sys
import tempfile
from pathlib import Path

import atomic_io as aio

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not ok else ""))


def _os_err(winerror=None, errno=None):
    e = OSError(errno, os.strerror(errno) if errno else "err")
    if winerror is not None:
        e.winerror = winerror
    return e


class _Recorder:
    """A replace stand-in: fails N times with the given error, then
    succeeds; records the delays the helper slept between tries."""

    def __init__(self, fail_times, err):
        self.fail_times = fail_times
        self.err = err
        self.calls = 0
        self.delays = []

    def __call__(self, tmp, target):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.err
        return None


class _Sleeper:
    def __init__(self):
        self.t = []

    def __call__(self, d):
        self.t.append(d)


def _run(fail_times, err, attempts=5):
    rec = _Recorder(fail_times, err)
    sl = _Sleeper()
    replace_atomic_out = aio.replace_atomic("t", "f", attempts=attempts,
                                            base_delay=0.05,
                                            sleep=sl, replace=rec)
    return rec, sl, replace_atomic_out


# ── the retry math ───────────────────────────────────────────────────
try:
    rec, sl, _ = _run(3, _os_err(winerror=32))
    check("WinError 32 (sharing violation) retries and succeeds on the "
          "4th try — the heal is in-process",
          rec.calls == 4 and sl.t == [0.05, 0.1, 0.2], (rec.calls, sl.t))

    rec, sl, _ = _run(1, _os_err(winerror=5))
    check("WinError 5 (rename loser) retries once and succeeds — the "
          "50ms first backoff",
          rec.calls == 2 and sl.t == [0.05], (rec.calls, sl.t))

    rec, sl, _ = _run(1, _os_err(errno=13))
    check("[Errno 13] (the errno rendering of the same family) retries "
          "— PermissionError is contention here",
          rec.calls == 2 and sl.t == [0.05], (rec.calls, sl.t))

    rec, sl, _ = _run(2, _os_err(errno=32))
    check("Errno 32 (POSIX sharing shape) retries",
          rec.calls == 3 and sl.t == [0.05, 0.1], (rec.calls, sl.t))

    # exhaustion: the LAST contention error raises unchanged
    rec = _Recorder(99, _os_err(winerror=5))
    sl = _Sleeper()
    exhausted = None
    try:
        aio.replace_atomic("t", "f", attempts=3, base_delay=0.05,
                           sleep=sl, replace=rec)
    except OSError as _exc:
        exhausted = _exc
    check("exhaustion raises the contention error after the bounded "
          "attempts (the caller's fail-soft contract still applies)",
          rec.calls == 3 and sl.t == [0.05, 0.1]
          and exhausted is rec.err, (rec.calls, sl.t, exhausted))

    # non-contention: immediate raise, zero retries
    boom = OSError(2, "no such file")
    rec = _Recorder(99, boom)
    sl = _Sleeper()
    raised = None
    try:
        aio.replace_atomic("t", "f", attempts=5, sleep=sl, replace=rec)
    except OSError as e:
        raised = e
    check("a non-contention error (missing tmp) raises IMMEDIATELY — "
          "a bug is never retried into a delay",
          raised is boom and rec.calls == 1 and sl.t == [],
          (raised, rec.calls, sl.t))

    # sanity: the doubling never exceeds the budget
    rec, sl, _ = _run(4, _os_err(winerror=32))
    check("the backoff doubles (50ms → 400ms total across 4 waits) — "
          "bounded, no multiplier surprises",
          sl.t == [0.05, 0.1, 0.2, 0.4] and rec.calls == 5, sl.t)

    # ── the smoke leg: the REAL os.replace on REAL files ────────────
    _d = Path(tempfile.mkdtemp())
    (_d / "tmp").write_text("hello", encoding="utf-8")
    aio.replace_atomic(_d / "tmp", _d / "target")
    check("smoke: the real os.replace path lands the file (the helper "
          "is a drop-in, not a simulation)",
          (_d / "target").read_text(encoding="utf-8") == "hello")
finally:
    fails = [r for r in results if not r[1]]
rep = BASE / "artifacts" / "atomicio" / "_atomicio_drive.txt"
rep.parent.mkdir(parents=True, exist_ok=True)
rep.write_text("\n".join(f"{'PASS' if ok else 'FAIL'}  {n}" +
                         (f"  [{d}]" if d and not ok else "")
                         for n, ok, d in results)
               + f"\n\nRESULT: {len(results) - len(fails)} passed, "
                 f"{len(fails)} failed\n", encoding="utf-8")
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
