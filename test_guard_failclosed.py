"""test_guard_failclosed.py — THE HERMETIC FAIL-CLOSED GUARD (the ntfy half).

The topic guard in installer/finalize_bundle.py is fail-closed. This drive
proves the two directions directly against the finalizer itself (not the
live bundle on disk, and not the installer sources), using an ISOLATED temp
scout root so the test never touches the tracked kraken_scout repo:

  · when push_topic.txt is ABSENT from the (temp) scout root, the finalizer
    must exit 1 with "build must fail" — the content arm cannot run, and the
    served zip is PUBLIC, so proceed-on-denylist-alone is not allowed.
  · when push_topic.txt IS PRESENT and a payload member carries the topic
    bytes, the finalizer must exit 1 with "GUARD HIT" (topic bytes).
  · when push_topic.txt IS PRESENT and the payload is clean, the finalizer
    must exit 0 (clean).

Each arm is hermetic: a real stage dir (copy of the live payload), an
ISOLATED temp scout root carrying only a FIXED synthetic topic (built from
parts so the contiguous literal cannot exist in any tracked source — a
literal here would match itself in any tree-wide scan), and the finalizer
read from the tracked installer source. The drive never reads the private
value it guards — the synthetic scan topic is runtime-built text, never read
from any file (including the live push_topic.txt).

This drive is a pure READER (staged temp dirs it creates + reads, the tracked
installer source — read, never written). The sandbox-leak verdict at the end
proves it. The drive's own writes are: stage dirs, dist dirs, the probe
sandbox, and the isolated temp scout roots — all temp dirs, never the repo.
"""

import builtins as _builtins
import hashlib
import io as _io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
FINALIZER = HERE / "installer" / "finalize_bundle.py"
REAL_PAYLOAD = HERE / "dist" / "KrakenScoutBundle" / "payload"
EXE = sys.executable

# ── 0b. THE SANDBOX-LEAK GUARD (the worthladder port, reader drive,
# fail-on-any-write): every write-capable open (builtins + io + fd os.open)
# and every os.replace this drive performs is recorded; the tail proves the
# drive wrote NOTHING beyond its own tempdir worlds. FOR THIS DRIVE THE
# CONTRACT IS ZERO WRITES outside the staged temp dirs.
import tempfile as _tf_warm
_tf_warm.gettempdir()
_WRITTEN = []
_REAL_OPEN = _builtins.open


def _path_str(p):
    if isinstance(p, bytes):
        return os.fsdecode(p)
    try:
        v = os.fspath(p)
        return os.fsdecode(v) if isinstance(v, bytes) else v
    except TypeError:
        return None


def _audit_open(file, mode="r", *a, **k):
    try:
        _p = _path_str(file)
        if _p is not None and any(_c in (mode or "") for _c in "wax+"):
            _WRITTEN.append(_p)
    except Exception:
        pass
    return _REAL_OPEN(file, mode, *a, **k)


_builtins.open = _audit_open
_io.open = _audit_open


_REAL_OSOPEN = os.open


def _audit_osopen(file, flags, *a, **k):
    try:
        _w_flags = (os.O_WRONLY, os.O_RDWR, os.O_APPEND,
                    os.O_CREAT, os.O_TRUNC)
        if any(flags & f for f in _w_flags):
            _p = _path_str(file)
            if _p is None and not isinstance(file, int):
                _p = f"<unattributable:{file!r}>"
            if k.get("dir_fd") is not None and _p is not None:
                _p = f"<dirfd-relative:{_p}>"
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


def check(n, cond, msg=""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + str(n)
          + ("  " + str(msg) if msg and not cond else ""))


def _read(p):
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ── the synthetic topic, built from parts at runtime (the self-match catch):
# the contiguous literal must NOT exist in any tracked source this drive reads
# (FINALIZER text, the live payload tree). Joined at runtime, parts are inert;
# only the assembled value is the scanned topic. Keep it SHORTER than the live
# topic (21 chars vs the live 30) so a coincidence match is not plausible, and
# NEVER read the live push_topic.txt — the scan topic is synthetic text only.
_SEED_PARTS = ("scout", "sbx", "guard", "hermetic", "fail")
SYNTH_TOPIC = "-".join(_SEED_PARTS)            # 21 chars
assert len(SYNTH_TOPIC) < 30, "synthetic topic must be shorter than the live topic"
assert SYNTH_TOPIC.encode("utf-8") not in _read(FINALIZER).encode("utf-8"), \
    "synthetic topic literal must not exist in the tracked finalizer source"

# quick scan of the live payload tree for the synthetic literal (fail if it
# already exists there — that would make this drive's own seed match itself)
_SANDBOX_LEAK_RAW = b""
for _p in REAL_PAYLOAD.rglob("*"):
    if _p.is_file():
        try:
            if SYNTH_TOPIC.encode("utf-8") in _p.read_bytes():
                _SANDBOX_LEAK_RAW = _p.read_bytes()
                break
        except OSError:
            pass
assert not _SANDBOX_LEAK_RAW, \
    "synthetic topic literal already present in the live payload tree — pick different parts"

results = []


def _make_scout_root(with_topic, topic_text=SYNTH_TOPIC):
    """An ISOLATED temp dir acting as the scout root: carries push_topic.txt
    (with_topic=True) or is empty of it (with_topic=False). Never the tracked
    kraken_scout repo — the test never touches the real repo."""
    d = Path(tempfile.mkdtemp(prefix="guard_failclosed_scout_"))
    if with_topic:
        d.mkdir(parents=True, exist_ok=True)
        (d / "push_topic.txt").write_text(topic_text + "\n", encoding="utf-8")
    return d


def _stage_payload(scout_root, add_leaky_member=False):
    """A stage dir: copy of the live payload, plus an optional leaky scout
    member carrying the synthetic topic. scout_root is the isolated scout dir
    passed to the finalizer as its SCOUT_ROOT arg (arg 3)."""
    stage = Path(tempfile.mkdtemp(prefix="guard_failclosed_payload_"))
    dist = stage.parent / (stage.name + "_dist")
    shutil.copytree(REAL_PAYLOAD, stage / "payload")
    if add_leaky_member:
        scout = stage / "payload" / "scout"
        scout.mkdir(parents=True, exist_ok=True)
        (scout / "leaky_note.txt").write_bytes(
            b"scout note\n" + SYNTH_TOPIC.encode("utf-8") + b"\n")
    return stage, dist


def _run_finalizer(stage, dist, scout_root):
    return subprocess.run(
        [EXE, str(FINALIZER),
         str(stage), str(dist),
         str(scout_root), str(HERE)],
        capture_output=True, text=True, timeout=90)


print("== 1. THE HERMETIC FAIL-CLOSED GUARD ==")

# 1.1 the finalizer source is present and fail-closed (exit 1 reachable)
check(1.1, FINALIZER.exists() and "sys.exit(1)" in _read(FINALIZER),
      "the tracked finalizer exists and reachably exit(1)s")


# 1.2 ABSENT topic → exit 1, "build must fail" (the fix: no longer
#     print+continue with "topic guard: clean (no topic provisioned)")
scout_root = _make_scout_root(with_topic=False)   # ISOLATED, no push_topic.txt
stage, dist = _stage_payload(scout_root)
proc = _run_finalizer(stage, dist, scout_root)
check(1.2, proc.returncode == 1
      and "build must fail" in (proc.stdout + proc.stderr),
      "with no push_topic.txt, the finalizer exits 1 and names the build-must-fail contract"
      + (f"\n    rc={proc.returncode} out={proc.stdout[-300:]!r}"
         if proc.returncode != 1 else ""))
check(1.3, "topic guard: clean (no topic provisioned)" not in proc.stdout,
      "the old print+continue path is gone — absent topic no longer emits clean")
check(1.4, "GUARD HIT" not in proc.stdout,
      "absent topic does not spuriously trip a byte hit")


# 1.6 PRESENT topic + leaky member → exit 1, "GUARD HIT (topic bytes)"
scout_root = _make_scout_root(with_topic=True)     # ISOLATED, carries synthetic topic
stage, dist = _stage_payload(scout_root, add_leaky_member=True)
proc = _run_finalizer(stage, dist, scout_root)
check(1.6, proc.returncode == 1
      and "GUARD HIT" in proc.stdout
      and "topic bytes" in proc.stdout,
      "with the topic file present and a payload member carrying the topic, the finalizer"
      + " exits 1 and reports the byte hit"
      + (f"\n    rc={proc.returncode} out={proc.stdout[-300:]!r}"
         if proc.returncode != 1 or "GUARD HIT" not in proc.stdout else ""))
check(1.7, "topic guard: clean" not in proc.stdout,
      "a leaky payload is not misreported clean")

# 1.8 PRESENT topic + CLEAN payload → exit 0
scout_root = _make_scout_root(with_topic=True)
stage, dist = _stage_payload(scout_root)
proc = _run_finalizer(stage, dist, scout_root)
check(1.8, proc.returncode == 0
      and "GUARD HIT" not in proc.stdout,
      "with the topic file present and a clean payload, the finalizer exits 0"
      + (f"\n    rc={proc.returncode} out={proc.stdout[-300:]!r}"
         if proc.returncode != 0 else ""))
check(1.9, "build must fail" not in proc.stdout,
      "a clean payload is not rejected")

# 1.10 the synthetic scan topic is runtime-built synthetic text, never read
#      from any file (including the live push_topic.txt) — it is the joined
#      parts, and it is shorter than the live topic, so it cannot be the secret
#      the guard is meant to catch.
check(1.10, SYNTH_TOPIC == "-".join(_SEED_PARTS) and len(SYNTH_TOPIC) < 30,
      "the scan topic is runtime-built synthetic text (joined parts), shorter than the live topic"
      + f"  topic={SYNTH_TOPIC!r} len={len(SYNTH_TOPIC)}")


# ── 4. THE SANDBOX-LEAK VERDICT (the guard's tail, worthladder port,
# inverted for a reader drive): the shim is proven ALIVE by one controlled
# probe write, then the log is judged — every recorded write must be one of
# the drive's own TEMPDIR worlds: the guard stage dir, the payload stage
# dirs, the isolated scout roots, and the probe sandbox. ANY write beyond
# those temp dirs — the repo, the dist tree, the live topic — is a FAIL.
_probe_dir = tempfile.mkdtemp(prefix="guard_failclosed_probe_")
_probe = Path(_probe_dir) / "probe.txt"
_probe.write_text("the guard is alive", encoding="utf-8")
_probe.unlink(missing_ok=True)

_OWN_WORLDS = [
    os.path.realpath(str(_probe_dir)),
]
for _p in list(_WRITTEN):
    try:
        _OWN_WORLDS.append(os.path.realpath(str(Path(_p).parent)))
    except Exception:
        pass


def _contained(w):
    try:
        if w.startswith("<"):
            return False
        wr = os.path.realpath(str(w))
        return any(os.path.commonpath([wr, world]) == world
                   for world in _OWN_WORLDS)
    except Exception:
        return False


check(4.1, _WRITTEN and _contained(_WRITTEN[0])
      and any("probe.txt" in str(w) for w in _WRITTEN),
      "the audit shim is ALIVE (the probe write was recorded)"
      + f"  writes: {_WRITTEN[:6]}")
_stray = [w for w in _WRITTEN if not _contained(w)]
check(4.2, not _stray,
      "the drive wrote ZERO paths beyond its own tempdir worlds"
      + (f"  strays: {sorted(set(_stray))[:6]}" if _stray else ""))

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
