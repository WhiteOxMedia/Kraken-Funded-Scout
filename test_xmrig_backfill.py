"""test_xmrig_backfill.py - THE CARRY-SHAPE HEALER DRIVE (Oct 30).

The wide bundle shape depends on the UNTRACKED xmrig toolchain tree,
and when its exe vanishes every rebuild degrades to the slim shape
with no red anywhere - the shape contracts honestly PASS both shapes,
so the degradation was SILENT by design. The healer closes that:
tools/backfill_xmrig.py re-stages the pinned release whenever the
local payload is missing, and tools/rebuild_bundle_watch.py calls it
at the top of every watch cycle.

This drive pins the whole wiring the same way test_sweep_wiring and
test_sibling_wiring pin theirs:

  1. THE THREE PIN SOURCES AGREE: the URL/zip-hash/exe-hash pins are
     ONE contract with THREE carriers (installer/install.ps1's
     $WantHash + URL, test_bundle.py's carried-exe pin 4.5, and
     backfill_xmrig.py). If any source drifts, this drive REDs before
     a healer stages a mismatched build or a rebuild ships one.
  2. THE WATCHER CALLS THE HEALER: statically - the watch cycle's
     first action runs backfill (before the drift decision, so the
     shape can't degrade past one cycle); dynamically - a present
     payload is an honest quiet no-op that never rewrites bytes.
  3. THE HEALER FAILS CLOSED (hermetic, no network): a zip that does
     not hash to the pin is refused before extraction; an extracted
     exe that does not hash to the pin is refused before staging; a
     present exe is never overwritten; a genuine pinned build IS
     staged byte-exactly. Feeding bytes directly through the test
     seam means no drive ever fetches the release.

Prints "RESULT: N passed, M failed". Skips honestly if
tools/backfill_xmrig.py is absent (the healer's own checkout foot).
"""
import hashlib
import io
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE
HEALER = REPO / "tools" / "backfill_xmrig.py"
WATCHER = REPO / "tools" / "rebuild_bundle_watch.py"
INSTALLER = REPO / "installer" / "install.ps1"
BUNDLE_DRIVE = REPO / "test_bundle.py"

URL = ("https://github.com/xmrig/xmrig/releases/download/v6.26.0/"
       "xmrig-6.26.0-windows-x64.zip")
ZIP_SHA256 = ("bba8097cb37d9b458a1cb1137876b27cde6740d17fe4ccbc086ba"
              "07d87d9e147")
EXE_SHA256 = ("6fa80698d7268f6e88aa88c06fb27ee99e1bcee747c2e76911e62"
              "06a5b1aeeb3")
results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


if not HEALER.is_file():
    print("SKIP: tools/backfill_xmrig.py not present - nothing to pin")
    print("RESULT: 0 passed, 0 failed")
    sys.exit(0)

sys.path.insert(0, str(REPO / "tools"))
import backfill_xmrig  # noqa: E402  (the module under test - loaded,
                       # not substring-matched: its constants are the
                       # live pins, a split-literal cannot hide drift)

mod = backfill_xmrig

# ── 1. THE THREE PIN SOURCES AGREE ──────────────────────────────────
check("1a the healer's loaded URL constant is the pinned release the "
      "installer fetches",
      mod.URL == URL,
      "one release asset, named identically in the healer's LIVE "
      "constant and install.ps1 - a changed URL heals a different "
      "build than the customer's install fetches")
check("1b the healer's loaded zip pin is $WantHash",
      mod.ZIP_SHA256 == ZIP_SHA256, "the zip hash gate BEFORE extraction")
check("1c the healer's loaded exe pin is the carried-exe pin",
      mod.EXE_SHA256 == EXE_SHA256, "the exe hash gate BEFORE staging")

ps1_src = INSTALLER.read_text(encoding="utf-8", errors="replace")
check("1d install.ps1's own pins still name the same URL + $WantHash",
      URL in ps1_src.replace("&", "") and ZIP_SHA256 in ps1_src,
      "the installer's fetch leg is the third carrier of the same "
      "three-pin contract")
drive_src = BUNDLE_DRIVE.read_text(encoding="utf-8", errors="replace")
check("1e test_bundle.py 4.5 still pins the carried exe to the same "
      "exe hash",
      EXE_SHA256 in drive_src,
      "the shape drive and the healer must never accept different "
      "builds")

# ── 2. THE WATCHER CALLS THE HEALER ─────────────────────────────────
w_src = WATCHER.read_text(encoding="utf-8", errors="replace")
check("2a the watcher imports backfill_xmrig (sibling in tools/)",
      "import backfill_xmrig" in w_src,
      "the healer's import is the wiring; drop it and the degradation "
      "returns to silent")
check("2b the watch cycle runs the call before the drift stamp is "
      "consulted",
      "_backfill_if_missing()" in w_src.split("def main", 1)[-1]
      and w_src.split("def main", 1)[-1].index("_backfill_if_missing()")
      < w_src.split("def main", 1)[-1].index("built = _stamp()"),
      "healing must not depend on HEAD drift - a missing payload with "
      "no drift would still degrade the NEXT build")
py = REPO / ".venv" / "Scripts" / "python.exe"
if not py.is_file():
    py = Path(sys.executable)
r = subprocess.run([str(py), "-X", "utf8", str(HEALER)],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=120, cwd=str(REPO))
check("2c the healer runs on the REAL tree and answers present when "
      "the payload exists",
      r.returncode == 0 and "present:" in (r.stdout or ""),
      f"rc={r.returncode}: {(r.stdout or '').strip()[:120]}"
      f" {(r.stderr or '').strip()[:120]}")

# ── 3. THE HEALER FAILS CLOSED (hermetic: bytes in, no fetch) ───────
# (the module is already loaded at the top - same instance, same pins)


def _fake_zip(exe_bytes):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xmrig-6.26.0/xmrig.exe", exe_bytes)
    return buf.getvalue()


with tempfile.TemporaryDirectory() as td:
    repo = Path(td)
    logs = []
    check("3a a zip whose bytes do not hash to the pin is refused "
          "BEFORE extraction (nothing staged)",
          (backfill_xmrig.stage_from_zip(
              _fake_zip(b"not the pinned exe"), repo=repo,
              log=logs.append) == ""
           and not (repo / "xmrig").exists()),
          "the zip-hash gate is the first line; a mismatched asset "
          "must never reach the tree")

    # exe-gate: a correctly-hashed ZIP wrap of a WRONG exe body. The
    # zip's own hash will mismatch the zip pin too, so to reach the
    # exe gate the drive feeds the bytes through a patched zip-hash
    # source: build a fake zip, then hash-swap via the module's
    # constants ONLY if the body gate would otherwise be unreachable.
    logs = []
    bad_exe_zip = _fake_zip(b"wrong exe body")
    # The honest path for a wrong exe: craft the zip so its own sha
    # matches the ZIP pin by constructor is impossible without the
    # real asset - so the drive pins the EXE gate through the staged
    # source order instead: swap the constants on the instance.
    saved_zip_pin = backfill_xmrig.ZIP_SHA256
    backfill_xmrig.ZIP_SHA256 = hashlib.sha256(bad_exe_zip).hexdigest()
    try:
        out = backfill_xmrig.stage_from_zip(
            bad_exe_zip, repo=repo, log=logs.append)
    finally:
        backfill_xmrig.ZIP_SHA256 = saved_zip_pin
    check("3b an extracted exe that does not hash to the pin is "
          "refused BEFORE staging",
          out == "" and not (repo / "xmrig" / "xmrig-6.26.0"
                             / "xmrig.exe").exists(),
          "the exe-hash gate is the second line: even a zip hash "
          "passing through a patched gate cannot stage a wrong exe")

    # 3c: the genuine pinned build staged byte-exactly. A locally
    # built zip can never hash to the PUBLISHED release zip's pin
    # (that is 3a's own finding), so this leg patches ONLY the zip
    # gate and keeps the exe gate on the REAL pin - the exact
    # guarantee that matters for staging.
    real = (REPO / "xmrig" / "xmrig-6.26.0" / "xmrig.exe")
    if real.is_file():
        good = real.read_bytes()
        assert hashlib.sha256(good).hexdigest() == EXE_SHA256, \
            "the local tree's exe itself drifted from the pin"
        logs = []
        saved_zip_pin = backfill_xmrig.ZIP_SHA256
        backfill_xmrig.ZIP_SHA256 = hashlib.sha256(
            _fake_zip(good)).hexdigest()
        try:
            staged_ok = backfill_xmrig.stage_from_zip(
                _fake_zip(good), repo=repo, log=logs.append) == "staged"
        finally:
            backfill_xmrig.ZIP_SHA256 = saved_zip_pin
        staged_path = (repo / "xmrig" / "xmrig-6.26.0" / "xmrig.exe")
        staged_bytes = staged_path.read_bytes()
        again_logs = []
        saved_zip_pin = backfill_xmrig.ZIP_SHA256
        backfill_xmrig.ZIP_SHA256 = hashlib.sha256(
            _fake_zip(good)).hexdigest()
        try:
            backfill_xmrig.stage_from_zip(
                _fake_zip(good), repo=repo, log=again_logs.append)
        finally:
            backfill_xmrig.ZIP_SHA256 = saved_zip_pin
        never = ("never overwritten" in " ".join(again_logs))
        check("3c a genuine pinned build stages byte-exactly and the "
              "second cycle never rewrites it",
              staged_ok and staged_bytes == good and never,
              "two cycles, one write - the present-exe guard is the "
              "never-mutate rule")
    else:
        print("SKIP: 3c needs the local toolchain exe (absent here); "
              "3a/3b still prove both fail-closed gates")

n_pass = results.count(True)
n_fail = results.count(False)
print(f"RESULT: {n_pass} passed, {n_fail} failed")
sys.exit(1 if n_fail else 0)
