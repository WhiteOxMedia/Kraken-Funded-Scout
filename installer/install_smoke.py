"""install_smoke.py - THE INSTALLER'S WINDOWS SMOKE DRIVE (Oct 29).

THE PROBLEM IT GATES. Three straight customer incidents (the re-nested
"xmrig/xmrig/6260" layout, a Defender quarantine, a setup script that
never downloaded anything) all surfaced on a machine the build box had
never tested the install on - and install.ps1's own try/catch makes the
worst case SILENT: a failed xmrig fetch only warns, so the install
"succeeds" with no miner anywhere. This drive turns each of those
failures into a red CI gate instead of a customer support thread.

WHAT IT PROVES, in order:

  1. THE PINS PARSE: the pinned URL, the pinned SHA256 and the exact
     install layout are read OUT OF install.ps1 itself - the surface
     that CREATES the layout - never retyped here. A pin change is one
     edit, not a second hidden literal; a failure to parse IS the
     failure mode (someone renamed a variable) and says so.
  2. THE PINNED ASSET IS STILL REAL: the pinned URL is downloaded and
     hashed right here. A 404 (the release asset this line once named
     did exactly that) or a swapped asset fails HERE, on the runner,
     not on a customer.
  3. THE ZIP CARRIES THE LAYOUT: exactly one xmrig.exe under the leaf
     folder the installer extracts into.
  4. THE EXACT PATH: after install.ps1 ran into a FRESH target in the
     drive mode its own header documents (-NoUi -SkipVenv), xmrig.exe
     sits at <target>/<parsed layout>/xmrig.exe - the one location
     miner.py, scout/app.py and system_check.py all require. Nothing
     EXECUTES the binary: Defender is live on GitHub's Windows runners
     and can quarantine a miner exe outright; byte equality below is
     the deterministic proof the runner can give.
  5. THE BYTES LANDED: the installed exe hashes equal to the pinned
     zip's inner xmrig.exe - not a rebuilt or partially quarantined
     binary.
  6. NO DOUBLE NEST: <target>/xmrig carries exactly one child, the
     pinned leaf folder - the re-nested customer layout from the
     incidents ("xmrig/xmrig/6260") is structurally impossible after
     this install.
  7. THE TREES: scout/app.py and the monero_project name contract are
     staged beside the miner (test_bundle.py pins the name; here it is
     re-proven end to end).

WHY NOT THE BUNDLE BUILDER. installer/build_bundle.ps1 needs the
sibling kraken_scout checkout and an untracked push_topic.txt, neither
of which a CI clone has - and it copies the LOCAL xmrig payload when
present, skipping the exact fetch path customers most often hit (the
bundle that shipped without one). So the CI job stages the bundle-root
LAYOUT (install.ps1 + payload/scout + payload/monero_project) from
the checkout with no xmrig payload at all, deliberately exercising the
pinned-URL fetch branch end to end.

Run (CI, the scout-install-smoke job):
    <stage bundle layout>  install.ps1 -Target <fresh dir> -NoUi -SkipVenv
    python installer/install_smoke.py --installer <staged install.ps1>
        --target <fresh dir>
Run (parse-only, any OS; what the pins must still parse as):
    python installer/install_smoke.py --check

House conventions: check(n, cond, why), RESULT line, sys.exit.
"""
import argparse
import hashlib
import io
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

B = chr(92)          # the backslash install.ps1 writes its layout with
passed = 0
failed = 0


def check(n, cond, why):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
    mark = "PASS" if cond else "FAIL"
    print(f"  [{n}] {mark}  {why}")
    return cond


def report():
    print(f"\nRESULT: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


def parse_installer(text):
    """The three installer pins, from the installer's own literals."""
    url = re.search(r'[$]Url\s*=\s*"([^"]+)"', text)
    want = re.search(r'[$]WantHash\s*=\s*"([0-9a-zA-Z]{64})"', text)
    rel = re.search(r'[$]DstXmrig\s*=\s*Join-Path [$]Target\s+"([^"]+)"', text)
    clean = rel.group(1).replace(B, "/") if rel else None
    return (url.group(1) if url else None,
            want.group(1).lower() if want else None,
            clean)


def pins_section(url, want, rel):
    print("== 1. THE PINS PARSE ==")
    ok = check(1, url is not None,
               "the pinned xmrig URL parses out of install.ps1")
    ok &= check(2, want is not None,
                "the pinned xmrig SHA256 parses out of install.ps1")
    ok &= check(3, rel is not None,
                "the exact install layout (the $DstXmrig literal) parses "
                "out of install.ps1")
    if all((url, want, rel)):
        parts = rel.split("/")
        ok &= check(4, len(parts) >= 2,
                    f"layout {rel!r} is <parent dir>/<leaf folder> with "
                    "both halves pinned")
        print(f"\n  pins: url={url}\n"
              f"        sha256={want}\n"
              f"        layout={rel!r} (exe at <target>/{rel}/xmrig.exe)")
    else:
        parts = None
        print("\n  A pin stopped parsing: the pin block in install.ps1 "
              "changed shape. Update THIS file's regexes consciously, "
              "never loosen them.")
    return parts


def full_drive(target, url, want, rel):
    parts = rel.split("/")
    parent, leaf = parts[0], parts[-1]
    xmrig_root = target / parent
    exe = target / rel / "xmrig.exe"

    print("\n== 2. THE PINNED ASSET IS STILL REAL ==")
    data = None
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "scout-install-smoke/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
    except Exception as e:                     # noqa: BLE001 - the finding
        print(f"  (download refused: {e})")
    if not check(5, data is not None,
                 "the pinned URL answers (a 404 here is the 404 a customer "
                 "would have hit)"):
        report()
    got = hashlib.sha256(data).hexdigest()
    if not check(6, got == want,
                 f"the pinned asset hashes to the pin ({got[:16]}... vs "
                 f"{want[:16]}...)"):
        report()

    print("\n== 3. THE ZIP CARRIES THE LAYOUT ==")
    inner_name, inner_bytes = None, None
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        raws = [n for n in z.namelist()
                if n.replace(B, "/").startswith(leaf + "/")
                and n.lower().endswith("xmrig.exe")]
        cands = [n.replace(B, "/") for n in raws]
        if check(7, len(cands) == 1,
                 f"exactly one xmrig.exe under {leaf}/ in the pinned zip "
                 f"(found {cands})"):
            inner_name = cands[0]
            inner_bytes = z.read(raws[0])
    if inner_bytes is None:
        report()

    print("\n== 4. THE EXACT PATH ==")
    exists = exe.is_file()
    if exists:
        check(8, True, f"xmrig.exe sits at the exact contract path ({exe})")
    else:
        check(8, False,
              f"xmrig.exe is MISSING from the exact contract path ({exe}) "
              "- install.ps1 ran but did not place it")
        report()
    check(9, exe.stat().st_size > 500_000,
          "the placed binary has real size (> 0.5 MB), not an empty or "
          "quarantine stub")

    print("\n== 5. THE BYTES LANDED ==")
    installed = exe.read_bytes()
    ho = hashlib.sha256(installed).hexdigest()
    hi = hashlib.sha256(inner_bytes).hexdigest()
    check(10, ho == hi,
          f"the installed exe is byte-for-byte the pinned zip's "
          f"{inner_name} ({ho[:16]}...)")

    print("\n== 6. NO DOUBLE NEST ==")
    kids = sorted(p.name for p in xmrig_root.iterdir()) \
        if xmrig_root.is_dir() else []
    kids_note = ", ".join(kids) if kids else "none - the extract went nowhere"
    check(11, bool(kids) and all(k.startswith(leaf) for k in kids),
          f"children of <target>/{parent} (must all live in the pinned "
          f"leaf folder {leaf}): {kids_note}")
    check(12, (xmrig_root / leaf).is_dir(),
          f"the leaf folder sits directly under <target>/{parent} - the "
          "re-nested customer layout from the incidents is structurally "
          "impossible here")

    print("\n== 7. THE TREES ==")
    check(13, (target / "scout" / "app.py").is_file(),
          "the Scout tree staged (target/scout/app.py)")
    check(14, (target / "monero_project" / "installer"
               / "install.ps1").is_file()
          and (target / "monero_project").is_dir(),
          "the monero_project NAME contract staged (test_bundle.py's "
          "one-brain transport fans out from that folder earlier)")


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(
        description="THE INSTALLER'S WINDOWS SMOKE DRIVE (Oct 29)")
    ap.add_argument("--installer", default=str(here / "install.ps1"),
                    help="install.ps1 to parse (default: the repo's own)")
    ap.add_argument("--target",
                    help="the fresh install target the smoke installed "
                         "into; omitted => parse-only")
    ap.add_argument("--check", action="store_true",
                    help="parse-only: prove the pins still parse, touch no "
                         "network and no target (runs on any OS)")
    args = ap.parse_args()

    installer = Path(args.installer)
    if not installer.is_file():
        print(f"FAIL  installer not found: {installer}")
        sys.exit(1)
    text = installer.read_text(encoding="utf-8", errors="strict")

    url, want, rel = parse_installer(text)
    parts = pins_section(url, want, rel)
    if not parts:
        report()
    if args.check or not args.target:
        if not args.check:
            print("\n(no --target given: parse-only)")
        report()

    full_drive(Path(args.target), url, want, rel)
    report()


if __name__ == "__main__":
    main()
