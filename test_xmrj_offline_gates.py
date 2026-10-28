"""test_xmrj_offline_gates.py — THE OFFLINE-GATE BOUNDARY (Oct 27).

WHY THIS EXISTS. The XMRJ offline drives come in two classes, and confusing them
is how a green CI job comes to mean nothing:

  * WHAT A CLONE CAN RUN — the wiring (test_anchor_wiring.py), the devnet
    suite's own contract (test_xmrj_devnet_e2e.py's offline half), the RPC
    layer (tests/xmrj_rpc_resilience.ts) and the endpoint contract
    (test_xmrj_rpc_endpoint.py). Sources, manifests and pins: a push can break
    them, so a push must be able to catch it.
  * WHAT NEEDS A BUILD — test_xmrj_build_artifacts.py and
    `tools/xmrj_loadtest.ts --offline` read `target/`, and the artifact-side
    half of the wiring drive reads `target/deploy/<program>-keypair.json`.
    `target/` is gitignored BY DESIGN: those keypairs hold the private halves
    of the program addresses, so no clone contains them, no CI job can
    regenerate the ids the crates declare, and 896 bytes of sbf toolchain are
    not a push gate. These are HOST-side drives: they belong where a build has
    happened.

The second class is the dangerous one, because a drive that cannot run is one
step away from a drive that reports success without running. Both of these
already refuse an unbuilt checkout in their own words — and that refusal is the
property this drive pins, in a copy of the tree with no `target/`, so it holds
whether or not the machine running it has built anything:

  1. each build-dependent drive REFUSES an unbuilt copy: non-zero exit AND its
     own message naming `anchor build` (a drive that exits 0 after printing a
     refusal would sail through any CI step that merely runs it)
  2. the host-side path still runs them: tools/xmrj_devnet_run.sh invokes the
     artifact drive and the harness, so "not in CI" does not mean "nowhere"
  3. the CI workflow runs the drives a clone CAN run, and BUILDS the programs in
     a second job so the artifact drive runs on a push too — the jobs exist where
     they claim to be, and section 3 fails if either is dropped from the pipeline
     or the build step loses the rlib cleanup that a warm rebuild needs
  4. RED-FIRST: each property above is shown to be able to fail, by breaking it
     in a copy of the tree and re-running this file against the copy

READ-ONLY apart from temporary copies it removes again: it builds nothing,
deploys nothing, signs nothing, and never sends a request to a cluster.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# A capured refusal's own text is printed back as a FAIL's detail, and a Windows
# console is cp1252: one undecodable byte in that text must not kill the drive
# with UnicodeEncodeError instead of reporting the check that went red.
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:                                          # noqa: BLE001
    pass

HERE = Path(__file__).resolve().parent
# Section 4 re-runs this file against a mutated COPY of the tree by pointing the
# root elsewhere; every path below is relative to it.
ROOT = Path(os.environ.get("XMRJ_OFFLINE_GATES_ROOT") or HERE)
_SELF = Path(__file__).resolve()

ARTIFACT_DRIVE = "test_xmrj_build_artifacts.py"
HARNESS = "tools/xmrj_loadtest.ts"
RUNNER = "tools/xmrj_devnet_run.sh"
WORKFLOW = ".github/workflows/ci.yml"

# What an unbuilt copy needs to look like the real tree. The crate directories
# matter even though the drives refuse early: a guard that moves must meet the
# same sources it would meet on a host.
FILES = (
    "Anchor.toml",
    "rust-toolchain.toml",
    "package.json",
    "tsconfig.json",
    "test_anchor_wiring.py",
    "test_xmrj_build_artifacts.py",
    "test_xmrj_devnet_e2e.py",
    "tests/xmrj_devnet_e2e.ts",
    "tests/xmrj_rpc_resilience.ts",
    "tools/xmrj_loadtest.ts",
    "tools/rpc_resilience.ts",
    "tools/xmrj_devnet_run.sh",
    ".github/workflows/ci.yml",
)
CRATES = ("xmrj_treasury", "xmrj_jackpot", "xmrj_governance")

results: list[tuple[bool, str, object]] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    results.append((bool(ok), name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))


def read(rel: str) -> str:
    path = ROOT / rel
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def copy_unbuilt_tree(mutate: tuple[str, str, str] | None = None) -> Path:
    """A copy of the tree with no `target/` — what a fresh clone looks like.

    The copy lives INSIDE the repository on purpose: ts-node and the pinned
    client dependency have to resolve from node_modules, which a system temp
    directory cannot see, and a refusal has to be produced the way CI produces
    it, not with a missing module standing in for a missing `.so`. The caller
    removes it again; `mutate` applies one (file, old, new) edit to the copy.
    """
    root = Path(tempfile.mkdtemp(prefix=".xmrj-offline-gates-", dir=str(ROOT)))
    try:
        for rel in FILES:
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if (ROOT / rel).is_file():
                shutil.copy2(ROOT / rel, dst)
        for crate in CRATES:
            src = ROOT / crate
            if src.is_dir():
                shutil.copytree(src, root / crate,
                                ignore=shutil.ignore_patterns("__pycache__"))
        if mutate is not None:
            rel, old, new = mutate
            if not (root / rel).is_file():
                raise ValueError(f"{rel} is not part of this drive's copy, so its "
                                 f"mutation cannot be applied")
            text = (root / rel).read_text(encoding="utf-8")
            if old not in text:
                raise ValueError(f"mutation no longer applies to {rel}")
            (root / rel).write_text(text.replace(old, new), encoding="utf-8")
    except Exception:                                      # noqa: BLE001
        shutil.rmtree(root, ignore_errors=True)
        raise
    return root


def run(argv: list[str] | str, cwd: Path | None = None) -> tuple[int, str]:
    """(exit code, combined output). A string is run through the shell, which is
    what makes `npx` (a .cmd on Windows, a script elsewhere) resolvable."""
    proc = subprocess.run(
        argv, cwd=str(cwd or ROOT), shell=isinstance(argv, str), timeout=300,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# ── 1. the build-dependent drives refuse an unbuilt checkout ─────────────
_fresh = copy_unbuilt_tree()
try:
    _rc, _out = run([sys.executable, str(_fresh / ARTIFACT_DRIVE)])
    check(
        "1.1 test_xmrj_build_artifacts.py REFUSES an unbuilt checkout — it exits "
        "non-zero and says so, rather than reporting a green fleet on a tree whose "
        "programs were never built (the failure mode its own docstring names)",
        _rc != 0 and "cannot continue without artifacts" in _out,
        f"rc {_rc}; last line: {_out.strip().splitlines()[-1] if _out.strip() else 'no output'}",
    )
finally:
    shutil.rmtree(_fresh, ignore_errors=True)

_fresh = copy_unbuilt_tree()
try:
    _npx = shutil.which("npx") or "npx"
    _rc, _out = run(f'"{_npx}" ts-node "{_fresh / HARNESS}" --offline')
    check(
        "1.2 `tools/xmrj_loadtest.ts --offline` REFUSES an unbuilt checkout too — "
        "its plan needs target/idl/*.json, and it names the fix instead of planning "
        "scenarios it cannot drive",
        _rc != 0 and "anchor build" in _out,
        f"rc {_rc}; last line: {_out.strip().splitlines()[-1] if _out.strip() else 'no output'}",
    )
finally:
    shutil.rmtree(_fresh, ignore_errors=True)

# ── 2. the host-side path still runs them ───────────────────────────────
runner = read(RUNNER)
check(
    "2.1 the runbook executor runs the artifact drive — the build-dependent half is "
    "gated where a build exists, so \"not in CI\" does not mean \"never\"",
    ARTIFACT_DRIVE in runner,
    "tools/xmrj_devnet_run.sh never runs it",
)
check(
    "2.2 the runbook executor runs the harness live (its --offline mode is part of "
    "the same file, and its live half is what the report's §11.5 is evidence for)",
    HARNESS in runner,
    "tools/xmrj_devnet_run.sh never runs it",
)

# ── 3. CI runs the drives a clone CAN run ───────────────────────────────
workflow = read(WORKFLOW)
_job = re.search(r"\n  xmrj-offline-drives:\n", workflow)
check(
    "3.1 the CI workflow has a job that runs the wiring drive and the suite's own "
    "offline half — the two drives a clone can run, and the two whose regression a "
    "push would otherwise only meet on a live devnet run",
    bool(_job)
    # Anchored to the end of its own step line: a renamed or disabled path
    # (`..._wiring.py.disabled`) still CONTAINS the old one as a substring, and
    # a check that cannot tell those apart is a check that cannot fail.
    and re.search(r"^\s*run: python test_anchor_wiring\.py$", workflow, re.M)
    and re.search(r"^\s*run: python test_xmrj_devnet_e2e\.py$", workflow, re.M),
    "the workflow does not run both as their own steps",
)
check(
    "3.2 that job is in the pipeline gate's `needs`, so it cannot be skipped while "
    "the pipeline still reports success",
    bool(re.search(r"needs:[\s\S]*?- xmrj-offline-drives", workflow)),
    "xmrj-offline-drives is not in the ci job's needs",
)

# The other half of the artifact drive: the .so/IDL checks need a BUILD, and CI
# now does one — with placeholder program keys and `--ignore-keys`, so the two
# identity checks report themselves SKIPPED (report §11.14 explains why no secret
# is used: an address IS a public key, so a valid keypair file for a declared id
# requires its private half).
_build_job = re.search(
    r"\n  xmrj-build-artifacts:\n([\s\S]*?)(?=\n  [a-z-]+:\n|\Z)", workflow
)
_build_body = _build_job.group(1) if _build_job else ""
check(
    "3.3 the CI workflow BUILDS the programs and runs the artifact drive on that "
    "build — with `anchor build --ignore-keys` over placeholder keypairs (never a "
    "program private key), and XMRJ_PLACEHOLDER_KEYS telling the drive to skip the "
    "keypair identity check instead of inventing a pass (check 3.4 still runs: "
    "Anchor stamps the IDL address from declare_id!, measured)",
    "anchor build --ignore-keys" in _build_body
    and "cargo build-sbf" in _build_body
    and re.search(r"^\s*run: python test_xmrj_build_artifacts\.py$", _build_body, re.M)
    is not None
    and re.search(r"XMRJ_PLACEHOLDER_KEYS: \"1\"", _build_body) is not None,
    "the build job is missing the build, the drive, or the placeholder declaration",
)
check(
    "3.4 the build job is in the pipeline gate's `needs` too, for the same reason",
    bool(re.search(r"needs:[\s\S]*?- xmrj-build-artifacts", workflow)),
    "xmrj-build-artifacts is not in the ci job's needs",
)
# The two things the job's COST measurement (report §11.14) proved it needs.
# Without the rlib cleanup, the second and later builds of a program die on a
# warm tree: `could not find `cpi` in `xmrj_jackpot`` plus a global_allocator
# conflict — measured, and the reason the runbook's §3 bullet exists. Without the
# --rev pin, the toolchain cache is unsound: it restores an avm binary, so a
# moving HEAD would make a cache hit a different tool from a cache miss.
check(
    "3.5 the build step removes the stale unhashed libxmrj_*.rlib before the "
    "per-program rebuild — measured: without it the next program's build fails on "
    "a warm tree with `could not find `cpi` in `xmrj_jackpot``",
    "rm -f target/sbpf-solana-solana/release/deps/libxmrj_*.rlib" in _build_body,
    "the build step does not clear the stale rlibs",
)
check(
    "3.6 avm is built from a PINNED commit, not the repository's HEAD — the "
    "toolchain cache restores a binary, so a moving HEAD would make a cache hit a "
    "different tool from a cache miss",
    re.search(r"cargo install avm --git \S+ --rev [0-9a-f]{7,40}\b", _build_body)
    is not None,
    "the avm install is not pinned to a revision",
)

# ── 4. RED-FIRST: each property above must be able to fail ──────────────
# The copies live inside the repository again (ts-node has to resolve), so each
# mutation below reuses `copy_unbuilt_tree`'s layout with one file replaced.
MUTATIONS: list[tuple[str, str, str, str]] = [
    ("1.1", ARTIFACT_DRIVE,
     'print("RESULT: cannot continue without artifacts — run `anchor build` first")\n    sys.exit(1)',
     'print("RESULT: cannot continue without artifacts — run `anchor build` first")\n    sys.exit(0)'),
    ("1.1", ARTIFACT_DRIVE,
     "RESULT: cannot continue without artifacts — run `anchor build` first",
     "RESULT: artifacts missing (continuing anyway)"),
    # the backslashes are the TS source's escaped backticks, not decoration
    ("1.2", HARNESS, "run \\`anchor build\\` first", "run the build first"),
    ("2.1", RUNNER, "test_xmrj_build_artifacts.py", "test_xmrj_artifact_drive_disabled.py"),
    ("3.1", WORKFLOW, "        run: python test_anchor_wiring.py\n",
     "        run: python test_anchor_wiring.py.disabled\n"),
    ("3.2", WORKFLOW, "      - xmrj-offline-drives\n", ""),
    ("3.3", WORKFLOW, "          XMRJ_PLACEHOLDER_KEYS: \"1\"\n", ""),
    ("3.4", WORKFLOW, "      - xmrj-build-artifacts\n", ""),
    ("3.5", WORKFLOW,
     "          rm -f target/sbpf-solana-solana/release/deps/libxmrj_*.rlib\n", ""),
    ("3.6", WORKFLOW, "--rev 85c5293e ", ""),
]


def run_against(root: Path) -> list[str]:
    """This file, against another root: the ids of the checks that went red."""
    env = dict(os.environ, XMRJ_OFFLINE_GATES_ROOT=str(root))
    proc = subprocess.run(
        [sys.executable, str(_SELF)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=str(root),
    )
    return [
        line.split()[1]
        for line in proc.stdout.splitlines()
        if line.startswith("FAIL") and len(line.split()) > 1
    ]


if not os.environ.get("XMRJ_OFFLINE_GATES_ROOT"):
    _control = copy_unbuilt_tree()
    try:
        _control_failed = run_against(_control)
    finally:
        shutil.rmtree(_control, ignore_errors=True)
    check(
        "4.1 control: the UNMUTATED copy of the tree passes every check above — "
        "without this, a mutation that goes red proves nothing about the check",
        not _control_failed,
        _control_failed,
    )

    _blind, _stale = [], []
    for _id, _rel, _old, _new in MUTATIONS:
        try:
            _root = copy_unbuilt_tree((_rel, _old, _new))
        except ValueError as err:
            _stale.append(str(err))
            continue
        try:
            _failed = run_against(_root)
        finally:
            shutil.rmtree(_root, ignore_errors=True)
        if _id not in _failed:
            _blind.append(f"{_id} kept passing once {_rel} was mutated")
    check(
        "4.2 every mutation in this section still applies to the file it names — a "
        "mutation that no longer matches has stopped testing anything",
        not _stale,
        _stale,
    )
    check(
        f"4.3 RED-FIRST: each of the {len(MUTATIONS)} mutations breaks its property in "
        f"a copy of the tree and trips the check that owns it",
        not _blind and not _stale,
        _blind,
    )

passed = sum(1 for ok, _, _ in results if ok)
failed = len(results) - passed
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
