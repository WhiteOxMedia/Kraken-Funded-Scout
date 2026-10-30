"""test_ci_both_shapes.py — THE BOTH-SHAPES SMOKE CONTRACT (Oct 30).

WHY THIS EXISTS. The scout-install-smoke job stages and installs the
bundle TWICE on purpose — once with NO xmrig payload (the slim shape,
so install.ps1's [3/4] takes the pinned-URL fetch branch) and once WITH
it (the carry shape, so [3/4] takes the robocopy branch — a branch no
clone can exercise, because the local xmrig/ tree is untracked). Both
steps live only as text in .github/workflows/ci.yml, so nothing today
stops an edit from quietly dropping one leg: the other leg still goes
green, the unproven branch never runs anywhere, and the next real
install hits the code path nobody showed.

This drive pins the job's text so BOTH legs are present, distinct, and
each one verified:

  1. THE JOB: scout-install-smoke exists, on windows-latest, with its
     15-minute cap (the carry leg doubles the install-phase work)
  2. SIX STEPS: the three slim steps (stage -> install -> smoke drive)
     and the three carry steps (stage -> install -> smoke drive),
     each named and present exactly once
  3. THE SLIM LEG IS SLIM: the staging explicitly excludes the
     untracked xmrig/ tree (an xmrig/ tree somewhere in the workspace
     would silently turn slim into carry and hide the fetch branch),
     the install targets KrakenScoutSmoke and nothing but it, and the
     smoke drive (installer/install_smoke.py) verifies THAT target
  4. THE CARRY LEG CARRIES: the staging copies the slim bundle ASIDE
     (never mutating the slim leg's inputs), parses the SAME $Url and
     $WantHash pins out of install.ps1 (install_smoke.py's rule — one
     edit is a pin change, never a second hidden literal), hashes the
     downloaded zip BEFORE a single byte is extracted, requires
     payload\\xmrig-6.26.0\\xmrig.exe, and drops the zip's config.json
     (the developer config never ships); the install targets a SECOND
     fresh directory (KrakenScoutSmokeCarry — no state leaks between
     the shapes) and asserts install.ps1's OWN leaf line
     "staged local xmrig 6.26.0", so the robocopy branch is PROVEN,
     not assumed; the smoke drive verifies THAT target
  5. TWO TARGETS, TWO VERIFICATIONS: a bundle staged once and verified
     twice cannot stand in for the pair
  6. THE DRIVE RIDES CI: static-invariants runs this drive, so every
     rule above is enforced on every push (removing the wiring step is
     itself a violation, pinned below)
  7. RED-FIRST: each rule is shown to be able to fail — the carry
     stage is deleted, the slim install is deleted, the carry verify
     is deleted, the leaf-line assertion is edited out, the
     hash-before-extract order is broken, and the CI wiring is
     removed, each in a mutated copy of the workflow text, with a
     violation expected for each

READ-ONLY apart from nothing at all: it PARSES (never executes)
ci.yml — no runner, no PowerShell, no network, no temp tree.
"""
import re
import sys

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:                                          # noqa: BLE001
    pass

WORKFLOW = ".github/workflows/ci.yml"
JOB = "scout-install-smoke"
SLIM_TARGET = "KrakenScoutSmoke"
CARRY_TARGET = "KrakenScoutSmokeCarry"
CARRY_LEAF = "staged local xmrig 6.26.0"
SMOKE_DRIVE = "install_smoke.py"
SELF_NAME = "test_ci_both_shapes.py"

SLIM = ("Stage the bundle layout the customer receives",
        "Install into the fresh directory (stage + miner-payload mode)",
        "The drive proves the pins, the exact path and the bytes")
CARRY = ("Stage the carry-shape bundle (the payload rides along)",
         "Install from the carry bundle (install.ps1's robocopy branch)",
         "Same smoke drive, carry branch")

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
    """{step name: body} — a step's text runs to the next `- name:`."""
    matches = list(STEP_HEAD.finditer(block))
    out = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(block)
        out[m.group(1).strip()] = block[m.end():end]
    return out


def violations(text):
    """Every contract breach of the workflow text, one line per cause.

    The mutations below re-run this SAME auditor, so SHOWING a rule
    fail-gladly exercises the real rule, not a shadow copy of it.
    """
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
    for name in SLIM + CARRY:
        if name not in bodies:
            v.append(f"step gone: '{name}'")

    # ── the slim leg is slim ──
    slim_stage = bodies.get(SLIM[0], "")
    slim_install = bodies.get(SLIM[1], "")
    slim_verify = bodies.get(SLIM[2], "")
    if not re.search(r"(?s)robocopy.*[/\.]XD[^\n]*\b[Xx]mrig\b", slim_stage):
        v.append(f"'{SLIM[0]}' no longer excludes the untracked xmrig/ tree "
                 "from robocopy — the checkout could silently turn slim "
                 "into carry and hide the fetch branch")
    if "Invoke-WebRequest" in slim_stage + slim_install:
        v.append("the slim leg downloads something itself — slim must let "
                 "install.ps1's own [3/4] fetch branch do the fetching")
    if SLIM_TARGET not in slim_install:
        v.append(f"the slim install no longer targets {SLIM_TARGET}")
    if CARRY_TARGET in slim_install:
        v.append("the slim install leaked onto the carry target")
    if SMOKE_DRIVE not in slim_verify or SLIM_TARGET not in slim_verify:
        v.append("the slim leg is not verified by the smoke drive against "
                 f"{SLIM_TARGET}")

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
    if not re.search(r"-match\s*\\?\"" + re.escape(CARRY_LEAF),
                     carry_install):
        v.append(f"the carry install's -match assertion no longer MATCHES "
                 f"'{CARRY_LEAF}' — the robocopy branch is claimed, "
                 "not proven")
    if SMOKE_DRIVE not in carry_verify or CARRY_TARGET not in carry_verify:
        v.append("the carry leg is not verified by the smoke drive against "
                 f"{CARRY_TARGET}")

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
                 "mention is not a green leg) — the contract above would "
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

    print("== 1. THE REAL WORKFLOW SATISFIES THE CONTRACT ==")
    clean = violations(src)
    check(1.1, not clean,
          "ci.yml's scout-install-smoke: both shapes staged, installed "
          "AND verified, each on its own fresh target", "; ".join(clean))

    print("\n== 2. RED-FIRST (every rule shown able to fail) ==")
    c = violations(drop_step(src, CARRY[0]))
    check(2.1, len(c) >= 2 and CARRY[0] not in job_block_of(drop_step(src,
          CARRY[0])),
          "deleting the carry STAGING step turns the audit red",
          "; ".join(c)[:180])
    c = violations(drop_step(src, SLIM[0]))
    check(2.2, len(c) >= 2,
          "deleting the slim STAGING step turns the audit red",
          "; ".join(c)[:180])
    c = violations(drop_step(src, SLIM[1]))
    check(2.3, len(c) >= 2,
          "deleting the slim INSTALL step turns the audit red",
          "; ".join(c)[:180])
    c = violations(drop_step(src, CARRY[2]))
    check(2.4, any("carry leg is not verified" in x for x in c),
          "deleting the carry VERIFY step turns the audit red",
          "; ".join(c)[:180])
    c = violations(without(src, CARRY_LEAF))
    check(2.5, any(CARRY_LEAF in x for x in c),
          f"editing out the branch assertion ('{CARRY_LEAF}') turns the "
          "audit red", "; ".join(c)[:180])
    c = violations(without(src, "Get-FileHash"))
    check(2.6, any("hashes the pinned zip BEFORE extraction" in x for x in c),
          "breaking the hash-before-extract order turns the audit red",
          "; ".join(c)[:180])
    mutated = re.sub(
        rf"^      - name: .*\n(?=        run: python {re.escape(SELF_NAME)}\n)",
        "", src, count=1, flags=re.M)
    mutated = without(mutated, f"        run: python {SELF_NAME}\n")
    c = violations(mutated)
    check(2.7, any(SELF_NAME in x and "static-invariants" in x for x in c),
          "removing THIS drive from static-invariants turns the audit red",
          "; ".join(c)[:180])
    c = violations(drop_step(src, CARRY[1]))
    check(2.8, any("carry install" in x or "carry leg" in x or CARRY_TARGET
                   in x for x in c),
          "deleting the carry INSTALL step turns the audit red",
          "; ".join(c)[:180])

    print(f"\nRESULT: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
