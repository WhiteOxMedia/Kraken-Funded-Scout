"""THE SCANNER SINGLETON — the arbiter is the kernel, not a check-then-write.

Nov 15: the scanner's guard was an O_EXCL pid file plus a tasklist liveness
probe. It refused duplicates in practice (the logs carry several refusals), but
it carries the shape the volatility engine's own guard was rebuilt away from: a
pid file can be unlinked between the "is it stale?" read and the O_EXCL create,
so two racing starts can both believe they won, and a recycle-time stale file
needs a takeover path that is itself a race.

Here the claim is an msvcrt byte-range lock (LK_NBLCK) on .scanner.lock:

  * atomic — two racers cannot both hold byte 0;
  * self-releasing — a killed scanner's lock vanishes with its process, so a
    stale pid file needs no retake race;
  * owner-only — only the holder writes its pid, and a refused racer never
    touches the content, so .scanner.pid always names the true holder;
  * fail-closed — no msvcrt, an unopenable arbiter, or an unstampable pid file
    all refuse rather than run unguarded.

.scanner.pid stays as the human-readable diagnostic (and what the launcher and
the logs quote); it is no longer the thing that decides. Paths are module
globals so a drive can rebind them into scratch and never touch the live pair.
"""
import os
import sys

try:
    import msvcrt
except ImportError:          # not Windows: no kernel byte-range lock
    msvcrt = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCANNER_PID_FILE = os.path.join(BASE_DIR, ".scanner.pid")
SCANNER_LOCK_FILE = os.path.join(BASE_DIR, ".scanner.lock")

_HOLDER_FD = None


def read_holder() -> str:
    """The pid the current holder stamped, or '' when there is none."""
    try:
        with open(SCANNER_PID_FILE, encoding="utf-8") as fh:
            return (fh.read() or "").strip()
    except OSError:
        return ""


def claim_lock() -> bool:
    """True when this process owns the scanner. False = do not start.

    False is a refusal in every case that is not a clean win: the pair is held,
    the holder cannot be verified, or the lock machinery itself failed. The
    caller exits loud.
    """
    global _HOLDER_FD
    if msvcrt is None:
        print("no msvcrt on this platform — the single-instance lock cannot be "
              "enforced — refusing to run unguarded")
        return False
    try:
        fd = os.open(SCANNER_LOCK_FILE, os.O_CREAT | os.O_RDWR)
    except OSError as exc:
        print(f"could not open the lock arbiter ({exc}) — refusing to run "
              f"unguarded")
        return False
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError:
        os.close(fd)
        old = read_holder()
        if old.isdigit():
            print(f"A scanner is already running (pid {old}). Not starting a "
                  f"second one -- the single-instance guard refused. Close "
                  f"that one first.")
        else:
            print("A scanner already holds the lock (its pid is not recorded). "
                  "Not starting a second one -- the single-instance guard "
                  "refused.")
        return False
    # OWNER: the kernel lock is ours for the process lifetime. Only now does
    # the pid file carry a pid, and it is plain text (never locked) so every
    # refused racer reads the true holder.
    try:
        with open(SCANNER_PID_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except OSError as exc:
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except Exception:
            pass
        os.close(fd)
        print(f"could not stamp the pid lock ({exc}) — refusing")
        return False
    _HOLDER_FD = fd
    return True


def release_lock() -> None:
    """Owner-only release. A non-owner's call is a no-op, so a stale pair left
    by a kill is simply claimed by the next boot (the kernel already released
    the dead holder's byte)."""
    global _HOLDER_FD
    fd = _HOLDER_FD
    if fd is None:
        return
    _HOLDER_FD = None
    try:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except Exception:
        pass
    try:
        os.close(fd)
    except Exception:
        pass
    for path in (SCANNER_PID_FILE, SCANNER_LOCK_FILE):
        try:
            if os.path.exists(path):
                os.unlink(path)
        except OSError:
            pass
    _ = sys  # read by callers for messages; kept for import symmetry
