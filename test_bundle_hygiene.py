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

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
