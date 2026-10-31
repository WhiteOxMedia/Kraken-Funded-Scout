"""test_ci_both_shapes.py - THE BOTH-SHAPES SMOKE CONTRACT (Oct 30).

WHY THIS EXISTS. The scout-install-smoke job stages and installs the
bundle TWICE on purpose - once with NO xmrig payload (the slim shape,
so install.ps1's [3/4] takes the pinned-URL fetch branch) and once WITH
it (the carry shape, so [3/4] takes the robocopy branch - a branch no
clone can exercise, because the local xmrig/ tree is untracked). Both
legs live only as text in .github/workflows/ci.yml, so nothing today
stops an edit from quietly dropping one leg: the other leg still goes
green, the unproven branch never runs anywhere, and the next real
install hits the code path nobody showed.

This drive pins the job's text so BOTH legs are present, distinct, and
each one verified:

  1. THE JOB: scout-install-smoke exists, on windows-latest, with its
     15-minute cap (the carry leg doubles the install-phase work)
  2. THE STEPS: the runner-hygiene Defender-exclusion step FIRST, then
     the three slim steps (stage -> install -> smoke drive) and the
     three carry steps (stage -> install -> smoke drive), each named
     and present exactly once
  3. THE SLIM LEG IS SLIM: the staging explicitly excludes the
     untracked xmrig/ tree (an xmrig/ tree somewhere in the workspace
     would silently turn slim into carry and hide the fetch branch),
     the install targets KrakenScoutSmoke and nothing but it, CAPTURES
     install.ps1's stdout to slim_install_transcript.txt (the $out =
     capture, $Rc judged, THEN the transcript written - cmdlets cannot
     clobber the captured slot), and the smoke drive verifies THAT
     target WITH --branch slim and --installer-output
  4. THE CARRY LEG CARRIES: the staging copies the slim bundle ASIDE
     (never mutating the slim leg's inputs), parses the SAME $Url and
     $WantHash pins out of install.ps1 (install_smoke.py's rule - one
     edit is a pin change, never a second hidden literal), hashes the
     downloaded zip BEFORE a single byte is extracted, requires
     payload\\xmrig-6.26.0\\xmrig.exe, and drops the zip's config.json
     (the developer config never ships); the install targets a SECOND
     fresh directory (KrakenScoutSmokeCarry - no state leaks between
     the shapes) and captures install.ps1's stdout to
     carry_install_transcript.txt the same way; the smoke drive
     verifies THAT target with --branch carry and --installer-output
  5. THE LEAF PIN LIVES IN THE DRIVE (the branch-blind assertion,
     pushed inside): install.ps1's captured stdout is the ONLY witness
     of which [3/4] leg actually took, so the leaf-line assertion lives
     in installer/install_smoke.py itself - its text parses BOTH leg
     lines out of install.ps1 ("staged local xmrig", the line CI's
     carry step used to -match on outside the drive, and "fetched
     xmrig", slim's mirror) and judges WHICH printed. The CI step that
     happened to run must NOT claim the branch itself: a leftover
     inline assertion is a violation
  6. TWO TARGETS, TWO VERIFICATIONS: a bundle staged once and verified
     twice cannot stand in for the pair
  7. THE DRIVE RIDES CI: static-invariants runs this drive, so every
     rule above is enforced on every push (removing the wiring step is
     itself a violation, pinned below)
  8. RED-FIRST: each rule is shown to be able to fail - the carry
     stage is deleted, the slim install is deleted, the carry verify
     is deleted, the transcript handoff is broken, the leaf-literal
     parsing is stripped out of install_smoke.py, the --branch arg is
     stripped from the drive, the hash-before-extract order is broken,
     the CI wiring is removed, the Defender-exclusion step is deleted,
     degraded into an exit, stripped of -ErrorAction Stop, and moved
     below the staging steps, each in a mutated copy, with a violation
     expected for each

  9. RUNNER HYGIENE, NOT A GATE: the Defender-exclusion step sits
     before either leg stages anything (an exclusion only helps if it
     predates the bytes), points Add-MpPreference -ExclusionPath at
     BOTH trees the legs use ($env:RUNNER_TEMP - both bundles and both
     targets - plus the workspace), keeps -ErrorAction Stop so a
     refusal actually throws into the catch, and the catch FAILS OPEN:
     one Write-Warning, an explicit '- continuing', never an exit -
     this gate verifies OUR staging logic, not Microsoft's scanner,
     and a refused exclusion must never fail the job

READ-ONLY apart from nothing at all: it PARSES (never executes)
ci.yml, never executes installer/install_smoke.py - no runner, no
PowerShell, no network, no temp tree.
"""
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:                                          # noqa: BLE001
    pass

WORKFLOW = ".github/workflows/ci.yml"
JOB = "scout-install-smoke"
SLIM_TARGET = "KrakenScoutSmoke"
CARRY_TARGET = "KrakenScoutSmokeCarry"
LEAF_STAGED = "staged local xmrig"
LEAF_FETCHED = "fetched xmrig"
SLIM_TRANSCRIPT = "slim_install_transcript.txt"
CARRY_TRANSCRIPT = "carry_install_transcript.txt"
SMOKE_DRIVE = "install_smoke.py"
SELF_NAME = "test_ci_both_shapes.py"
SMOKE_PATH = Path("installer") / SMOKE_DRIVE

SLIM = ("Stage the bundle layout the customer receives",
        "Install into the fresh directory (stage + miner-payload mode)",
        "The drive proves the SHAPE, the pins, the exact path and the bytes")
CARRY = ("Stage the carry-shape bundle (the payload rides along)",
         "Install from the carry bundle (install.ps1's robocopy branch)",
         "Same smoke drive, carry branch - the drive asserts the shape itself")
DEFENDER = "Defender exclusions for the smoke tree (harness hygiene)"

STEP_HEAD = re.compile(r"^      - name: (.+)$", re.M)

passed = failed = 0


def check(n, cond, why, detail=""):
    global passed, failed
    ok = bool(cond)
    print(f"{'PASS' if ok else 'FAIL'}  {n}  {why}"
          + (f"  ({detail})" if detail and not ok else ""))
    passed, failed = passed + ok, failed + (not ok)


def job_block_of(text, job=JOB):
    """The two-space job block, from its key to the next job key."""
    m = re.search(rf"^  {re.escape(job)}:.*?(?=^  \w[\w-]*:|\Z)",
                  text, re.MULTILINE | re.DOTALL)
    return m.group(0) if m else ""


def step_bodies(block):
    """{step name: body} - a step's text runs to the next `- name:`."""
    matches = list(STEP_HEAD.finditer(block))
    out = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(block)
        out[m.group(1).strip()] = block[m.end():end]
    return out


def leaf_violations(smoke):
    """The leaf pin lives in the drive, not in a CI step's claim.

    Anchored on the DEDICATED definitions (the regex constants and the
    argparse arg), not loose substring counts: a check that any mention
    would satisfy could survive a mutation that gutted the real one.
    """
    v = []
    if not re.search(r"STAGED_LEAF_RE\s*=", smoke):
        v.append(f"{SMOKE_PATH} no longer parses install.ps1's robocopy "
                 f"leg line ('{LEAF_STAGED}') out of the installer's own "
                 "literal - the carry branch assertion died")
    if not re.search(r"FETCHED_LEAF_RE\s*=", smoke):
        v.append(f"{SMOKE_PATH} no longer parses install.ps1's fetch leg "
                 f"confirmation ('{LEAF_FETCHED}') - the slim branch "
                 "assertion died")
    if not re.search(r'ap\.add_argument\("--installer-output"', smoke):
        v.append(f"{SMOKE_PATH} lost the --installer-output arg - nothing "
                 "hands the drive the stdout that witnesses the leg")
    if '"slim", "carry"' not in smoke:
        v.append(f"{SMOKE_PATH} lost the --branch slim|carry choice - it "
                 "can no longer say which shape's installer copy it ran")
    return v


def violations(text, smoke=None):
    """Every contract breach of the workflow text (and - pinned
    separately below - the drive's own text), one line per cause.

    The mutations below re-run this SAME auditor, so SHOWING a rule
    fail-gladly exercises the real rule, not a shadow copy of it.
    """
    if smoke is None:
        smoke = SMOKE_PATH.read_text(encoding="utf-8", errors="replace")
    v = []
    block = job_block_of(text, JOB)
    if not block:
        v.append(f"the '{JOB}' job is missing from {WORKFLOW}")
        return v
    if "windows-latest" not in block:
        v.append(f"{JOB} no longer runs on windows-latest")
    if not re.search(r"^    timeout-minutes: 15$", block, re.M):
        v.append(f"{JOB} lost its 15-minute cap (the carry leg needs it)")

    bodies = step_bodies(block)
    for name in (DEFENDER,) + SLIM + CARRY:
        if name not in bodies:
            v.append(f"step gone: '{name}'")

    # ── runner hygiene, not a gate ──
    hygiene = bodies.get(DEFENDER, "")
    if hygiene and not re.search(r"Add-MpPreference.*-ExclusionPath",
                                 hygiene, re.S):
        v.append("the Defender-exclusion step no longer adds an "
                 "Add-MpPreference ExclusionPath - a quarantine could "
                 "kill a leg mid-job over again")
    if "$env:RUNNER_TEMP" not in hygiene or "github.workspace" not in hygiene:
        v.append("the Defender-exclusion step no longer covers BOTH trees "
                 "the legs use ($env:RUNNER_TEMP - both bundles and both "
                 "targets - and the workspace)")
    if hygiene and "-ErrorAction Stop" not in hygiene:
        v.append("the Defender-exclusion step dropped -ErrorAction Stop: "
                 "a refused exclusion would skip the catch and go SILENT")
    catch = re.search(r"catch\s*\{(.*?)\}", hygiene, re.S) if hygiene \
        else None
    if not catch or "Write-Warning" not in catch.group(1) \
            or re.search(r"\b(exit|throw)\b", catch.group(1)):
        v.append("the Defender-exclusion step no longer FAILS OPEN (one "
                 "Write-Warning, no exit/throw) - it is harness hygiene, "
                 "not a gate: a refused exclusion must never go red")
    pos_hygiene = block.find(f"- name: {DEFENDER}")
    pos_staging = block.find(f"- name: {SLIM[0]}")
    if pos_hygiene != -1 and pos_staging != -1 \
            and pos_hygiene > pos_staging:
        v.append("the Defender-exclusion step no longer precedes the "
                 "staging steps - exclusions must predate the bytes")

    # ── the slim leg is slim ──
    slim_stage = bodies.get(SLIM[0], "")
    slim_install = bodies.get(SLIM[1], "")
    slim_verify = bodies.get(SLIM[2], "")
    if not re.search(r"(?s)robocopy.*[/\.]XD[^\n]*\b[Xx]mrig\b", slim_stage):
        v.append(f"'{SLIM[0]}' no longer excludes the untracked xmrig/ tree "
                 "from robocopy - the checkout could silently turn slim "
                 "into carry and hide the fetch branch")
    if "Invoke-WebRequest" in slim_stage + slim_install:
        v.append("the slim leg downloads something itself - slim must let "
                 "install.ps1's own [3/4] fetch branch do the fetching")
    if SLIM_TARGET not in slim_install:
        v.append(f"the slim install no longer targets {SLIM_TARGET}")
    if CARRY_TARGET in slim_install:
        v.append("the slim install leaked onto the carry target")
    if not (SLIM_TRANSCRIPT in slim_install
            and "Set-Content" in slim_install):
        v.append(f"the slim install no longer writes install.ps1's "
                 f"captured stdout to {SLIM_TRANSCRIPT} - the drive would "
                 "have to CLAIM the fetch branch instead of proving it")
    if SMOKE_DRIVE not in slim_verify or SLIM_TARGET not in slim_verify \
            or "--branch slim" not in slim_verify \
            or "--installer-output" not in slim_verify:
        v.append("the slim leg is not verified by the smoke drive against "
                 f"{SLIM_TARGET} with --branch slim --installer-output")

    # ── the carry leg carries ──
    carry_stage = bodies.get(CARRY[0], "")
    carry_install = bodies.get(CARRY[1], "")
    carry_verify = bodies.get(CARRY[2], "")
    if not ("smoke_bundle" in carry_stage
            and "smoke_bundle_carry" in carry_stage):
        v.append("carry staging does not copy the staged slim bundle aside "
                 "(the slim leg's inputs must stay untouched)")
    if "$Url" not in carry_stage or "$WantHash" not in carry_stage:
        v.append("carry staging stopped parsing the SAME $Url/$WantHash "
                 "pin block out of install.ps1 (one edit is a pin "
                 "change, never a second hidden literal)")
    hash_pos = carry_stage.find("Get-FileHash")
    expand_pos = carry_stage.find("Expand-Archive")
    if hash_pos == -1 or expand_pos == -1 or hash_pos > expand_pos:
        v.append("carry staging no longer hashes the pinned zip BEFORE "
                 "extraction (the fail-closed order)")
    if "xmrig-6.26.0\\xmrig.exe" not in carry_stage:
        v.append("carry staging no longer requires payload\\xmrig-6.26.0\\"
                 "xmrig.exe after extraction")
    if "config.json" not in carry_stage or "Remove-Item" not in carry_stage:
        v.append("carry staging no longer drops the developer config.json "
                 "(the [3/6] mirror)")
    if CARRY_TARGET not in carry_install:
        v.append(f"the carry install no longer targets {CARRY_TARGET}")
    if not (CARRY_TRANSCRIPT in carry_install
            and "Set-Content" in carry_install):
        v.append(f"the carry INSTALL step no longer writes install.ps1's "
                 f"captured stdout to {CARRY_TRANSCRIPT} - the drive, "
                 "which now owns the leaf-line assertion, would receive "
                 "nothing to judge")
    if "-match" in carry_install:
        v.append("the carry install still asserts the leaf line itself "
                 "(branch-blind, in a step that cannot say which shape's "
                 "installer copy it ran) - the drive owns it now")
    if SMOKE_DRIVE not in carry_verify or CARRY_TARGET not in carry_verify \
            or "--branch carry" not in carry_verify \
            or "--installer-output" not in carry_verify:
        v.append("the carry leg is not verified by the smoke drive against "
                 f"{CARRY_TARGET} with --branch carry --installer-output")

    # ── the leaf pin lives in the drive ──
    v.extend(leaf_violations(smoke))

    # ── two targets, two verifications ──
    # count COMMAND lines, not mentions: the carry stage's comments cite
    # install_smoke.py's rule by name
    n_verify = len(re.findall(rf"(?m)^\s*python installer\\{SMOKE_DRIVE}",
                              block))
    if n_verify != 2:
        v.append(f"expected exactly 2 smoke-drive invocations, found "
                 f"{n_verify}")

    # ── the smoke drive rides CI ──
    si = job_block_of(text, "static-invariants")
    if f"run: python {SELF_NAME}" not in si:
        v.append(f"static-invariants no longer RUNS {SELF_NAME} (a comment "
                 "mention is not a green leg) - the contract above would "
                 "hold nothing on a push")
    return v


def drop_step(text, name):
    """Delete one '- name:' step: its name line through its body."""
    pattern = (rf"^      - name: {re.escape(name)}$"
               rf"(?:[\s\S](?!      - name: ))+\n?")
    out, n = re.subn(pattern, "", text, count=1, flags=re.MULTILINE)
    return out if n else text


def without(text, needle):
    return text.replace(needle, "", 1)


def main():
    with open(WORKFLOW, encoding="utf-8") as f:
        src = f.read()
    # ci.yml is CRLF; the auditor and its mutations reason in LF, so
    # normalise ONCE at the read boundary (the file itself is untouched)
    src = src.replace("\r\n", "\n")
    smoke = SMOKE_PATH.read_text(encoding="utf-8", errors="replace")
    smoke_lf = smoke.replace("\r\n", "\n")

    print("== 1. THE REAL WORKFLOW SATISFIES THE CONTRACT ==")
    clean = violations(src, smoke_lf)
    check(1.1, not clean,
          "ci.yml's scout-install-smoke: both shapes staged, installed "
          "AND verified, each on its own fresh target, transcripts "
          "handed to the branch-aware drive",
          "; ".join(clean))

    print("\n== 2. RED-FIRST (every rule shown able to fail) ==")
    c = violations(drop_step(src, CARRY[0]), smoke_lf)
    check(2.1, len(c) >= 2 and CARRY[0] not in job_block_of(drop_step(src,
          CARRY[0])),
          "deleting the carry STAGING step turns the audit red",
          "; ".join(c)[:180])
    c = violations(drop_step(src, SLIM[0]), smoke_lf)
    check(2.2, len(c) >= 2,
          "deleting the slim STAGING step turns the audit red",
          "; ".join(c)[:180])
    c = violations(drop_step(src, SLIM[1]), smoke_lf)
    check(2.3, len(c) >= 2,
          "deleting the slim INSTALL step turns the audit red",
          "; ".join(c)[:180])
    c = violations(drop_step(src, CARRY[2]), smoke_lf)
    check(2.4, any("carry leg is not verified" in x for x in c),
          "deleting the carry VERIFY step turns the audit red",
          "; ".join(c)[:180])
    c = violations(without(src, CARRY_TRANSCRIPT), smoke_lf)
    check(2.5, any(CARRY_TRANSCRIPT in x for x in c),
          f"breaking the transcript HANDOFF ('{CARRY_TRANSCRIPT}') turns "
          "the audit red",
          "; ".join(c)[:180])
    c = violations(src, re.sub(r"STAGED_LEAF_RE\s*=\s*[^\n]+", "",
                               smoke_lf, count=1))
    check(2.6, any("robocopy" in x and "assertion" in x for x in c),
          f"stripping the robocopy leg literal's regex out of "
          f"{SMOKE_PATH} turns the audit red",
          "; ".join(c)[:180])
    c = violations(src, re.sub(r'ap\.add_argument\("--installer-output",',
                               "", smoke_lf, count=1))
    check(2.7, any("--installer-output" in x for x in c),
          f"stripping the --installer-output arg out of {SMOKE_PATH} "
          "turns the audit red",
          "; ".join(c)[:180])
    c = violations(without(src, "Get-FileHash"), smoke_lf)
    check(2.8, any("hashes the pinned zip BEFORE extraction" in x for x in c),
          "breaking the hash-before-extract order turns the audit red",
          "; ".join(c)[:180])
    mutated = re.sub(
        rf"^      - name: .*\n(?=        run: python {re.escape(SELF_NAME)}\n)",
        "", src, count=1, flags=re.M)
    mutated = without(mutated, f"        run: python {SELF_NAME}\n")
    c = violations(mutated, smoke_lf)
    check(2.9, any(SELF_NAME in x and "static-invariants" in x for x in c),
          "removing THIS drive from static-invariants turns the audit red",
          "; ".join(c)[:180])
    c = violations(drop_step(src, CARRY[1]), smoke_lf)
    check(2.10, any("carry install" in x or "carry leg" in x or CARRY_TARGET
                    in x for x in c),
          "deleting the carry INSTALL step turns the audit red",
          "; ".join(c)[:180])
    c = violations(drop_step(src, DEFENDER), smoke_lf)
    check(2.11, any("Defender" in x for x in c),
          "deleting the Defender-exclusion step turns the audit red",
          "; ".join(c)[:180])
    c = violations(without(src, "Write-Warning ("), smoke_lf)
    check("2.12", any("FAILS OPEN" in x for x in c),
          "degrading the exclusion refusal into an exit turns the audit "
          "red (harness hygiene, not a gate)", "; ".join(c)[:180])
    c = violations(without(src, "-ErrorAction Stop"), smoke_lf)
    check(2.13, any("-ErrorAction Stop" in x for x in c),
          "dropping -ErrorAction Stop (a refusal would go SILENT) turns "
          "the audit red", "; ".join(c)[:180])
    jb = step_bodies(job_block_of(src, JOB))
    step_text = (f"      - name: {DEFENDER}\n"
                 + jb[DEFENDER].rstrip() + "\n\n")
    moved = drop_step(src, DEFENDER)
    moved = moved.replace("\n  xmrj-rpc-contract:",
                          f"\n{step_text}  xmrj-rpc-contract:", 1)
    c = violations(moved, smoke_lf)
    check(2.14, any("precedes the " in x for x in c),
          "moving the Defender-exclusion step below the staging steps "
          "turns the audit red (exclusions must predate the bytes)",
          "; ".join(c)[:180])

    print(f"\nRESULT: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
