"""test_bundle_hygiene.py — THE TOPIC GUARD ON THE PUBLIC BUNDLE (Oct 7,
Phase 0 of the ntfy topic rotation, the monero half).

The pool serves dist/KrakenScoutBundle.zip UNAUTHENTICATED at
/downloads/KrakenScoutBundle.zip (server.py, no-auth route) — and the
builder ships `git archive kraken_scout HEAD` as the scout payload.
When the topic files were TRACKED at kraken HEAD, the public zip
carried the private ntfy topic (proven pre-cure: 3 entries —
push_topic.txt, _ntfydiag.txt, .workshop/context.md; anyone with the
URL could subscribe and read every BUY / GET OUT meant for the
operator's phone).

THE CURE is double:
  · kraken untracked the carriers (commit 3b95c02) — the archive door
    is the commit itself, and the commit no longer carries the topic;
  · the builder fail-closes via installer/finalize_bundle.py (wired as
    build_bundle.ps1 step [6/6]): denylist filenames, the .workshop/
    dir, and the LIVE topic bytes read from the scout's untracked
    push_topic.txt — never a hardcoded secret, so the guard follows
    every future rotation — before the zip + manifest + sidecar are
    written in one pass (the served artifact can never drift).

Pins, red-first (pre-cure: the builder had no finalize step and the
zip on disk carried the topic in 3 entries):
  1. the builder wires the finalizer (ps1 source contract)
  2. the finalizer fail-closes: denylist names + .workshop/ + the
     topic read from the scout file; no hardcoded topic anywhere in
     the installer sources
  3. the LIVE zip on disk: zero topic bytes, zero denylist entries,
     zero .workshop entries (skip-clean when the bundle is not built)
  4. the served surface stays consistent: manifest sha == zip bytes ==
     sidecar (the serve drive's contract, re-pinned here)
"""
import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCOUT = HERE.parent / "kraken_scout"
ZIP = HERE / "dist" / "KrakenScoutBundle.zip"
MANIFEST = HERE / "dist" / "KrakenScoutBundle.zip.json"
SHA_FILE = HERE / "dist" / "KrakenScoutBundle.zip.sha256"
DENY_NAMES = {"push_topic.txt", "_ntfydiag.txt"}

results = []

# ════════ 0b. THE SANDBOX-LEAK GUARD (the worthladder port, user ask
# Oct 8): every write-capable open (builtins + io + fd-level os.open)
# and every os.replace this drive performs is recorded; the tail proves
# the drive wrote NOTHING outside a probe sandbox. FOR THIS DRIVE THE
# CONTRACT IS ZERO WRITES: it is a pure READER (the builder sources,
# the public zip, the manifest — read, never touched), so any write at
# all is a regression, and the guard is proven ALIVE by one controlled
# probe write into a tempdir (the anti-vacuity inverse of the writer
# drives: their guard fails on zero writes, this one fails on any
# write it did not itself make). Passive by design — the audit never
# blocks a write, it JUDGES the log at the end.
import io as _io
import builtins as _builtins
import tempfile as _tf_warm
_tf_warm.gettempdir()   # warm the lazy tempdir BEFORE the shim: CPython's
                        # first gettempdir() fd-opens a probe file in the
                        # temp root to test writability — tempfile's own
                        # machinery, not this drive's write; warmed here
                        # it never pollutes the audit log

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


def check(n, cond, msg=""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + str(n)
          + ("  " + str(msg) if msg and not cond else ""))


def _read(p):
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# the live topic, read from the scout's UNTRACKED runtime file — this
# drive must never carry the secret it guards, and must follow every
# future rotation
topic = b""
tf = SCOUT / "push_topic.txt"
if tf.exists():
    topic = tf.read_bytes().strip()

ps1 = _read(HERE / "installer" / "build_bundle.ps1")
fin = _read(HERE / "installer" / "finalize_bundle.py")

print("== 1. THE BUILDER CONTRACT ==")
check(1.1, "finalize_bundle.py" in ps1,
      "build_bundle.ps1 wires the finalizer (the guard + zip step)")
check(1.2, bool(fin) and "push_topic.txt" in fin and "_ntfydiag.txt" in fin
      and ".workshop" in fin,
      "the finalizer denylists the topic carriers by NAME and dir")
check(1.3, bool(fin) and "scout" in fin and "push_topic.txt" in fin
      and "read_bytes" in fin,
      "the finalizer reads the LIVE topic from the scout's untracked "
      "file (follows every rotation, never hardcoded)")
check(1.4, bool(fin) and "sys.exit(1)" in fin,
      "the finalizer fail-closes (exit 1 on any guard hit)")
if topic:
    leak = [name for name, src in (("build_bundle.ps1", ps1),
                                   ("finalize_bundle.py", fin))
            if topic in src.encode("utf-8")]
    check(1.5, not leak,
          "no installer source carries the topic bytes (a hardcoded "
          "secret would re-leak it and trip the guard on its own "
          "build)" + (str(leak) if leak else ""))
else:
    check(1.5, True, "(no live topic provisioned — secret-scan skipped)")

print("== 2. THE LIVE ARTIFACT ON DISK ==")
if ZIP.exists():
    with zipfile.ZipFile(ZIP) as z:
        names = z.namelist()
        byte_hits, name_hits = [], []
        for n in names:
            if Path(n).name in DENY_NAMES or "/.workshop/" in n:
                name_hits.append(n)
                continue
            if topic:
                try:
                    if topic in z.read(n):
                        byte_hits.append(n)
                except Exception:
                    pass
    check(2.1, not byte_hits,
          f"zero topic bytes in the public zip ({len(names)} entries; "
          f"pre-cure this carried 3 topic entries)"
          + (str(byte_hits[:4]) if byte_hits else ""))
    check(2.2, not name_hits,
          "zero denylist carriers in the public zip"
          + (str(name_hits[:4]) if name_hits else ""))
else:
    check(2.1, True, "(bundle not built — zip absent, skip-clean)")
    check(2.2, True, "(bundle not built — zip absent, skip-clean)")

pd = HERE / "dist" / "KrakenScoutBundle" / "payload" / "scout"
if pd.is_dir():
    carriers = [f.name for f in pd.rglob("*")
                if f.name in DENY_NAMES or ".workshop" in f.parts]
    check(2.3, not carriers,
          "the staged payload dir carries no topic carriers"
          + (str(carriers[:4]) if carriers else ""))
else:
    check(2.3, True, "(payload dir absent, skip-clean)")

print("== 3. THE SERVED SURFACE STAYS CONSISTENT ==")
if ZIP.exists() and MANIFEST.exists() and SHA_FILE.exists():
    disk_sha = hashlib.sha256(ZIP.read_bytes()).hexdigest()
    info = json.loads(MANIFEST.read_text(encoding="utf-8"))
    check(3.1, info.get("sha256") == disk_sha,
          "manifest hash == the zip bytes on disk")
    check(3.2, SHA_FILE.read_text(encoding="ascii").strip() == disk_sha,
          "the .sha256 sidecar == the zip bytes on disk")
    check(3.3, bool(info.get("scout_head")) and bool(info.get("monero_head")),
          "the manifest publishes both build heads")
else:
    check(3.1, True, "(artifact set incomplete, skip-clean)")
    check(3.2, True, "(artifact set incomplete, skip-clean)")
    check(3.3, True, "(artifact set incomplete, skip-clean)")

# ════════ 4. THE SANDBOX-LEAK VERDICT (the guard's tail, the worthladder
# port, inverted for a reader drive): the shim is proven ALIVE by one
# controlled probe write into a tempdir (a drive-audit that records
# nothing proves nothing), then the log is judged — every recorded
# write must be that probe's tempdir path. ANY other write — a repo
# file, the dist tree, the live topic — is a FAIL: this drive audits
# the bundle by READING it, and a test that wrote what it audits would
# be the leak it exists to catch. Containment on RESOLVED paths
# (realpath), fail-closed on unresolvable/markers.
import tempfile as _tf
_probe_dir = _tf.mkdtemp(prefix="bundlehyg_probe_")
_probe = Path(_probe_dir) / "probe.txt"
_probe.write_text("the guard is alive", encoding="utf-8")
_probe.unlink(missing_ok=True)

_probe_r = os.path.realpath(str(_probe_dir))

def _contained(w):
    try:
        if w.startswith("<"):  # unattributable/dirfd markers: fail-closed
            return False
        return os.path.commonpath(
            [os.path.realpath(str(w)), _probe_r]) == _probe_r
    except Exception:
        return False

check(4.1, _WRITTEN and _contained(_WRITTEN[0]) and
      any("probe.txt" in str(w) for w in _WRITTEN),
      "the audit shim is ALIVE (the probe write was recorded) — "
      f"writes: {_WRITTEN[:3]}")
_stray = [w for w in _WRITTEN if not _contained(w)]
check(4.2, not _stray,
      "the drive wrote ZERO paths beyond the probe sandbox (a pure "
      "reader: the sources, the zip and the manifest are read, never "
      "touched)" + (f" strays: {sorted(set(_stray))[:5]}" if _stray else ""))

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
