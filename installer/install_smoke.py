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
     failure mode (someone renamed a variable) and says so. So are BOTH
     [3/4] leg lines: the robocopy leg's "staged local xmrig" leaf line
     and the fetch leg's "fetched xmrig" confirmation.
  2. THE SHAPE IT RAN: --branch slim|carry names which shape's installer
     copy this drive is standing over, and --installer-output hands the
     drive install.ps1's CAPTURED STDOUT from its install run - so the
     drive itself judges WHICH leg [3/4] actually took: slim must print
     the fetch leg's own confirmation and never the robocopy leaf line;
     carry must print the robocopy leaf line and never the fetch one.
     This is the branch-blind observation, pushed inside the drive: what
     CI's carry step used to assert outside it (an "-match" on a hand-
     copied literal, in a step that could not say which shape's installer
     copy it ran) is now asserted where the contract lives. The branch
     is asserted, not assumed - and the OTHER leg can no longer print a
     line it must not (a payload that leaks into the slim bundle would
     silently turn the fetch-leg proof into a robocopy run; that shows
     up HERE, flagged by the line that must not print).
  3. THE PINNED ASSET IS STILL REAL: the pinned URL is downloaded and
     hashed right here - in BOTH shapes, because the pin itself is
     branch-independent: carry's payload rode in from the same asset.
     A 404 (the release asset this line once named did exactly that) or
     a swapped asset fails HERE, on the runner, not on a customer.
  4. THE ZIP CARRIES THE LAYOUT: exactly one xmrig.exe under the leaf
     folder the installer extracts into.
  5. THE EXACT PATH: after install.ps1 ran into a FRESH target in the
     drive mode its own header documents (-NoUi -SkipVenv), xmrig.exe
     sits at <target>/<parsed layout>/xmrig.exe - the one location
     miner.py, scout/app.py and system_check.py all require. Nothing
     EXECUTES the binary: Defender is live on GitHub's Windows runners
     and can quarantine a miner exe outright; byte equality below is
     the deterministic proof the runner can give.
  6. THE BYTES LANDED: the installed exe hashes equal to the pinned
     zip's inner xmrig.exe - not a rebuilt or partially quarantined
     binary.
  7. NO DOUBLE NEST: <target>/xmrig carries exactly one child, the
     pinned leaf folder - the re-nested customer layout from the
     incidents ("xmrig/xmrig/6260") is structurally impossible after
     this install.
  8. THE TREES: scout/app.py and the monero_project name contract are
     staged beside the miner (test_bundle.py pins the name; here it is
     re-proven end to end).

WHY NOT THE BUNDLE BUILDER. installer/build_bundle.ps1 needs the
sibling kraken_scout checkout and an untracked push_topic.txt, neither
of which a CI clone has - and it copies the LOCAL xmrig payload when
present, skipping the exact fetch path customers most often hit (the
bundle that shipped without one). So the CI job stages the bundle-root
LAYOUT (install.ps1 + payload/scout + payload/monero_project) from
the checkout with no xmrig payload at all, deliberately exercising the
pinned-URL fetch branch end to end - and stages a second bundle WITH
the payload so the robocopy branch runs in CI too (test_ci_both_shapes
pins BOTH legs; the drive is what makes each leg prove which one it
actually took).

Run (CI, the scout-install-smoke job; steps, one per shape):
    <stage bundle layout>            slim: no payload. carry: with it.
    $out = powershell.exe ... -File install.ps1 -Target <fresh dir> -NoUi -SkipVenv
                                     (capturing stdout - the drive judges it)
    python installer/install_smoke.py --branch slim
        --installer <staged install.ps1>
        --installer-output <captured transcript> --target <fresh dir>
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

# The two [3/4] leg lines, parsed out of install.ps1 itself - never
# retyped here. The robocopy leg's leaf line is the one CI's carry step
# used to assert "-match" on, branch-blind; the fetch leg's
# confirmation is slim's mirror of the same assertion.
STAGED_LEAF_RE = r'Write-Host\s+"(\s*staged local xmrig [0-9.]+)"'
FETCHED_LEAF_RE = r'Write-Host\s+"(\s*fetched xmrig [0-9.]+)"'


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
    """The three installer pins plus both leg lines, from the
    installer's own literals."""
    url = re.search(r'[$]Url\s*=\s*"([^"]+)"', text)
    want = re.search(r'[$]WantHash\s*=\s*"([0-9a-zA-Z]{64})"', text)
    rel = re.search(r'[$]DstXmrig\s*=\s*Join-Path [$]Target\s+"([^"]+)"', text)
    staged = re.search(STAGED_LEAF_RE, text)
    fetched = re.search(FETCHED_LEAF_RE, text)
    clean = rel.group(1).replace(B, "/") if rel else None
    return (url.group(1) if url else None,
            want.group(1).lower() if want else None,
            clean,
            staged.group(1) if staged else None,
            fetched.group(1) if fetched else None)


def pins_section(url, want, rel, staged_leaf, fetched_leaf):
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
    ok &= check(5, staged_leaf is not None,
                "the robocopy leg's leaf line parses out of install.ps1 "
                "(the line CI used to assert on, branch-blind)")
    ok &= check(6, fetched_leaf is not None,
                "the fetch leg's confirmation line parses out of "
                "install.ps1 (slim's mirror of the same assertion)")
    if not (staged_leaf and fetched_leaf):
        print("\n  A leaf line stopped parsing: the [3/4] block in "
              "install.ps1 changed shape. Update THIS file's regexes "
              "consciously, never loosen them.")
    return parts


def leg_section(branch, staged_leaf, fetched_leaf, trans_path):
    """Which shape's installer copy ran, proven from ITS OWN stdout."""
    print("\n== 2. THE SHAPE IT RAN ==")
    if branch == "carry":
        print("  shape: carry - the payload rides along; install.ps1's "
              "[3/4] MUST take the robocopy leg")
    else:
        print("  shape: slim - no payload staged; install.ps1's [3/4] "
              "MUST take the pinned-URL fetch leg")
    if trans_path is None:
        check(7, False,
              "no --installer-output: the branch shape would only be "
              "CLAIMED, never proven - hand the drive install.ps1's "
              "captured stdout")
        return
    tr = Path(trans_path)
    if not check(7, tr.is_file(),
                 f"the installer transcript exists ({tr})"):
        return
    text = tr.read_text(encoding="utf-8", errors="replace")
    if branch == "carry":
        check(8, staged_leaf in text,
              f"the robocopy leaf line PRINTED ({staged_leaf!r}) - the "
              "fetch branch cannot have quietly run instead")
        check(9, fetched_leaf not in text,
              f"the fetch leg's confirmation ({fetched_leaf!r}) did NOT "
              "print - the pinned URL was never fetched")
    else:
        check(8, fetched_leaf in text,
              f"the fetch leg's own confirmation PRINTED "
              f"({fetched_leaf!r})")
        check(9, staged_leaf not in text,
              f"the robocopy leaf line ({staged_leaf!r}) did NOT print - "
              "no payload was there to robocopy")


def full_drive(target, url, want, rel, branch, staged_leaf, fetched_leaf,
               trans_path):
    parts = rel.split("/")
    parent, leaf = parts[0], parts[-1]
    xmrig_root = target / parent
    exe = target / rel / "xmrig.exe"

    leg_section(branch, staged_leaf, fetched_leaf, trans_path)

    print("\n== 3. THE PINNED ASSET IS STILL REAL ==")
    data = None
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "scout-install-smoke/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
    except Exception as e:                     # noqa: BLE001 - the finding
        print(f"  (download refused: {e})")
    if not check(10, data is not None,
                 "the pinned URL answers (a 404 here is the 404 a customer "
                 "would have hit)"):
        report()
    got = hashlib.sha256(data).hexdigest()
    if not check(11, got == want,
                 f"the pinned asset hashes to the pin ({got[:16]}... vs "
                 f"{want[:16]}...)"):
        report()

    print("\n== 4. THE ZIP CARRIES THE LAYOUT ==")
    inner_name, inner_bytes = None, None
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        raws = [n for n in z.namelist()
                if n.replace(B, "/").startswith(leaf + "/")
                and n.lower().endswith("xmrig.exe")]
        cands = [n.replace(B, "/") for n in raws]
        if check(12, len(cands) == 1,
                 f"exactly one xmrig.exe under {leaf}/ in the pinned zip "
                 f"(found {cands})"):
            inner_name = cands[0]
            inner_bytes = z.read(raws[0])
    if inner_bytes is None:
        report()

    print("\n== 5. THE EXACT PATH ==")
    exists = exe.is_file()
    if exists:
        check(13, True, f"xmrig.exe sits at the exact contract path ({exe})")
    else:
        check(13, False,
              f"xmrig.exe is MISSING from the exact contract path ({exe}) "
              "- install.ps1 ran but did not place it")
        report()
    check(14, exe.stat().st_size > 500_000,
          "the placed binary has real size (> 0.5 MB), not an empty or "
          "quarantine stub")

    print("\n== 6. THE BYTES LANDED ==")
    installed = exe.read_bytes()
    ho = hashlib.sha256(installed).hexdigest()
    hi = hashlib.sha256(inner_bytes).hexdigest()
    check(15, ho == hi,
          f"the installed exe is byte-for-byte the pinned zip's "
          f"{inner_name} ({ho[:16]}...)")

    print("\n== 7. NO DOUBLE NEST ==")
    kids = sorted(p.name for p in xmrig_root.iterdir()) \
        if xmrig_root.is_dir() else []
    kids_note = ", ".join(kids) if kids else "none - the extract went nowhere"
    check(16, bool(kids) and all(k.startswith(leaf) for k in kids),
          f"children of <target>/{parent} (must all live in the pinned "
          f"leaf folder {leaf}): {kids_note}")
    check(17, (xmrig_root / leaf).is_dir(),
          f"the leaf folder sits directly under <target>/{parent} - the "
          "re-nested customer layout from the incidents is structurally "
          "impossible here")

    print("\n== 8. THE TREES ==")
    check(18, (target / "scout" / "app.py").is_file(),
          "the Scout tree staged (target/scout/app.py)")
    check(19, (target / "monero_project" / "installer"
               / "install.ps1").is_file()
          and (target / "monero_project").is_dir(),
          "the monero_project NAME contract staged (test_bundle.py's "
          "one-brain transport fans out from that folder earlier)")


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(
        description="THE INSTALLER'S WINDOWS SMOKE DRIVE (Oct 29)")
    ap.add_argument("--branch", choices=("slim", "carry"), default="slim",
                    help="which shape's installer copy ran: slim stages "
                         "with NO xmrig payload (install.ps1's [3/4] must "
                         "take the pinned-URL fetch leg); carry stages "
                         "WITH it (the robocopy leg)")
    ap.add_argument("--installer", default=str(here / "install.ps1"),
                    help="install.ps1 to parse (default: the repo's own)")
    ap.add_argument("--installer-output",
                    help="file holding install.ps1's CAPTURED STDOUT from "
                         "its install run; the drive judges it to pin "
                         "which [3/4] leg actually took (the branch-blind "
                         "assertion lives HERE now, not in the CI step "
                         "that happened to run)")
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

    url, want, rel, staged_leaf, fetched_leaf = parse_installer(text)
    parts = pins_section(url, want, rel, staged_leaf, fetched_leaf)
    if not parts:
        report()
    if args.check or not args.target:
        if not args.check:
            print("\n(no --target given: parse-only)")
        report()

    full_drive(Path(args.target), url, want, rel, args.branch,
               staged_leaf, fetched_leaf, args.installer_output)
    report()


if __name__ == "__main__":
    main()
