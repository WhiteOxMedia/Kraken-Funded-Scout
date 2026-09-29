"""ATOMIC IO — the fleet's shared atomic-rename helper (Sep 29).

THE CONTRACT IT ADDS: a lost rename retries IN-PROCESS instead of
waiting for the machine's next tick. The stateclobber storm proved the
two Windows contention failures are transient and named:

  - WinError 5  (access denied)        — the loser of two concurrent
                                         os.replace calls onto one target;
  - WinError 32 (sharing violation)    — the target is mid-read by a
                                         fail-soft reader (or mid-swap);
  - PermissionError / [Errno 13]       — the errno rendering of the
                                         same access/share family.

A bounded exponential backoff (5 attempts, 50ms doubling) rides the
helper so EVERY production writer inherits the heal without each site
re-deriving it. Anything else (a missing tmp, a bad path) raises
IMMEDIATELY — a bug is never retried into a delay.

The helper is deliberately tiny and stdlib-only so the bot repo can
vendor a byte-exact copy (its interpreter never sees this repo).
`replace` and `sleep` are injectable: the drive pins the retry math
with recorders instead of real waits.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

# THE NAMED CONTENTION FAMILY (Windows winerror / POSIX errno renderings
# of "the rename lost the race — try again")
RETRY_WINERRORS = (5, 32)
RETRY_ERRNOS = (13, 32)

DEFAULT_ATTEMPTS = 5
DEFAULT_BASE_DELAY = 0.05          # seconds; 50ms doubling → ≤0.75s total

# THE RETRY TELEMETRY (the ops ask: structured metrics for every replace
# failure, retry attempt, and final outcome — the tuning data for the
# backoff budget and the early-warning signal for systemic pressure).
# Append-only jsonl, one line per try; every write is fail-soft —
# telemetry must never break a state save.
TELEMETRY_PATH = Path("artifacts") / "io_events.jsonl"


def _note(event: dict, path: Path | None = None) -> None:
    """Append one structured event; NEVER raises (a broken telemetry
    sink is invisible next to a broken state write)."""
    try:
        p = path or TELEMETRY_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")
    except Exception:
        pass


def _contention(exc: OSError) -> bool:
    """True only for the named, transient, retryable family."""
    winerror = getattr(exc, "winerror", None)
    if winerror is not None:
        return int(winerror) in RETRY_WINERRORS
    return exc.errno in RETRY_ERRNOS


def replace_atomic(tmp, target, *, attempts: int = DEFAULT_ATTEMPTS,
                   base_delay: float = DEFAULT_BASE_DELAY,
                   sleep=time.sleep, replace=os.replace,
                   note=None) -> None:
    """os.replace(tmp, target), retried on named contention.

    Raises the LAST contention error unchanged after `attempts` failed
    tries (the caller's existing cleanup/fail-soft conventions apply);
    any non-contention error raises immediately. `replace` and `sleep`
    are injection seams (the unit drive pins the math with recorders;
    the machine runs the real os.replace). Every try emits one
    structured telemetry line (op/attempt/error/outcome/elapsed) so
    contention pressure is measurable in production."""
    tmp, target = str(tmp), str(target)
    note = note or _note
    attempts = max(1, int(attempts))
    t0 = time.perf_counter()
    last: OSError | None = None
    for attempt in range(attempts):
        try:
            replace(tmp, target)
            if attempt:
                note({"op": "replace_atomic", "target": target,
                      "attempt": attempt, "outcome": "success",
                      "error": None,
                      "elapsed_ms": round((time.perf_counter() - t0)
                                          * 1000, 1),
                      "ts": datetime.now().astimezone().isoformat()})
            return
        except OSError as exc:
            if not _contention(exc):
                note({"op": "replace_atomic", "target": target,
                      "attempt": attempt, "outcome": "non_contention",
                      "error": repr(exc),
                      "elapsed_ms": round((time.perf_counter() - t0)
                                          * 1000, 1),
                      "ts": datetime.now().astimezone().isoformat()})
                raise
            last = exc
            note({"op": "replace_atomic", "target": target,
                  "attempt": attempt, "outcome": "retried",
                  "error": repr(exc),
                  "elapsed_ms": round((time.perf_counter() - t0)
                                      * 1000, 1),
                  "ts": datetime.now().astimezone().isoformat()})
            if attempt < attempts - 1:
                sleep(base_delay * (2 ** attempt))
    assert last is not None
    note({"op": "replace_atomic", "target": target,
          "attempt": attempts - 1, "outcome": "exhausted",
          "error": repr(last),
          "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
          "ts": datetime.now().astimezone().isoformat()})
    raise last
