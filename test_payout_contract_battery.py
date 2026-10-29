"""test_payout_contract_battery.py — THE PAYOUT-CONTRACT BATTERY ENTRY (Oct 28).

The contract the pool README documents ("The payout contract,
operator-facing, Oct 28") is three rules; this entry votes on all of
them and EMITS what it just verified as payout_contract.json — the
inventory the docs drive (test_payout_contract_docs.py) grades the
README section against. See "THE VERIFIED-INVENTORY EMISSION" below.
them with ONE result, executing the three drives that pin each rule
through THE POOL VENV — the battery-execution rule: pool code needs
the pool's dependencies (fastapi/starlette/requests), a sibling venv
does not carry them, exactly why the Scout battery's pool entry
executes this repo's live drive through this repo's venv.

  Entries and the rules they pin:

  test_vrsc_coin_surface.py      — Rule 1: the same-coin payout surfaces
                                   its coin, quantity and pending-treasury
                                   state on the ledger and both cards.
  test_vrsc_rate_stale.py        — Rule 2: the conversion refuses at a
                                   stale or snapshot-less rate, pays loudly
                                   in the warn window, and is retriable.
  test_vrsc_concurrent_claim.py  — Rule 3: one drawing, one payout under
                                   two concurrent claims (the claim lease).

Classification (the fleet's vocabulary, applied per child):

  PASS        green on the first run
  FLAKE       failed the first run, passed the rerun — a warning
  REGRESSION  failed both runs — this entry fails

HONEST-GREEN discipline: the entry never answers green with zero
checks — it sums the children's own parsed `RESULT:` lines and its
own result line is the sum, so the fleet/battery parse it exactly
like any other drive. Every child is hermetic by construction
(temp-db sandbox, kill-switched rate feeds, stubbed wallet RPC), so
the entry belongs in any hermetic lane — `--fast` included, a rare
honor for a live-contract-shaped entry.

SKIP semantics (the shared rule): the pool venv missing is the one
honest skip — the entry answers `SKIP: pool venv missing` +
`RESULT: 0 passed, 0 failed`, exit 0. A skipped entry is a named row,
never a silent green. A skipped run ALSO emits nothing: the inventory
is what THIS run verified, and an unverifiable run leaves the
artifact exactly as the last verified run left it.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import drivekit  # noqa: E402

# A Windows console reads cp1252; children stream Unicode. The fleet
# rule is that a drive must never matter what console it lands in —
# now one shared pin, drivekit's exact parent side.
drivekit.configure_console()

POOL_VENV = HERE.joinpath(".venv", "Scripts", "python.exe")
TIMEOUT_S = 300          # seconds per child run (drives finish in ~1-3 s)
RERUN_COOLDOWN_S = 0     # hermetic children: nothing rate-limited to cool

CHILDREN = [
    ("test_vrsc_coin_surface.py",     "Rule 1 — same-coin surfacing"),
    ("test_vrsc_rate_stale.py",       "Rule 2 — the stale-rate guard"),
    ("test_vrsc_concurrent_claim.py", "Rule 3 — the claim lease race"),
]

RESULT_RE = drivekit.RESULT_RE

# ═══ THE VERIFIED-INVENTORY EMISSION (Oct 28) ═══════════════════
# After grading the children, WHAT THIS RUN JUST VERIFIED is emitted
# as payout_contract.json: the three rules, the drive that pins each,
# the live classification and check counts, and the exact tokens the
# README's contract section MUST carry. test_payout_contract_docs.py
# grades README.md against this artifact, so doc drift and contract
# drift converge on one red row instead of two silent ones.
#
# The body is deterministic (sorted keys, no timestamps): a re-emit
# of an unchanged green run is byte-identical, so the artifact only
# moves when the verified facts move, and a commit carries it
# together with the change that moved it.
# ═════════════════════════════════════════════════════════════════

README_FILE = "README.md"
README_SECTION = "The payout contract"
OUT_PATH = HERE / "payout_contract.json"

RULE_DEFS = [
    {
        "rule": 1,
        "title": "the winner's coin decides (same-coin payout)",
        "child": "test_vrsc_coin_surface.py",
        "readme_facts": ["rule 1", "same-coin payout", "amount_xmr",
                          "amount_coin", "pending_manual_treasury",
                          "test_vrsc_coin_surface.py"],
    },
    {
        "rule": 2,
        "title": "no payout at a stale rate (the stale-rate guard)",
        "child": "test_vrsc_rate_stale.py",
        "readme_facts": ["rule 2", "stale-rate guard", "vrsc_conversion_guard",
                          "VRSC_RATE_STALE_S", "vrsc_rate_stale_warned",
                          "vrsc_rate_stale_blocked", "test_vrsc_rate_stale.py"],
    },
    {
        "rule": 3,
        "title": "one drawing, one payout (the claim lease)",
        "child": "test_vrsc_concurrent_claim.py",
        "readme_facts": ["rule 3", "claim lease", "acquire_claim_lease",
                          "claiming", "claim_race_lost", "300 s steal window",
                          "test_vrsc_concurrent_claim.py"],
    },
]
READ_TOGETHER = ["test_payout_contract_battery.py", "claim_window_days"]


def build_inventory(classes, per_child):
    """The inventory of THIS verified run: which child pinned which
    rule, at what counts, plus the README facts that must stay
    documented."""
    return {
        "inventory": "payout_contract",
        "generated_by": "test_payout_contract_battery.py",
        "readme": {"file": README_FILE, "section": README_SECTION},
        "read_together": READ_TOGETHER,
        "rules": [
            {
                "rule": d["rule"],
                "title": d["title"],
                "child": d["child"],
                "readme_facts": d["readme_facts"],
                "classification": classes.get(d["child"], "ungraded"),
                "checks": {"passed": per_child.get(d["child"], (0, 0))[0],
                           "failed": per_child.get(d["child"], (0, 0))[1]},
            }
            for d in RULE_DEFS
        ],
        "totals": {
            "rules": len(RULE_DEFS),
            "children_checks_passed": sum(
                v[0] for k, v in per_child.items()
                if k in [d["child"] for d in RULE_DEFS]),
            "children_checks_failed": sum(
                v[1] for k, v in per_child.items()
                if k in [d["child"] for d in RULE_DEFS]),
        },
    }


def emit_inventory(inv):
    """Write payout_contract.json deterministically: only when the
    body actually differs (a green re-run is byte-identical, so the
    artifact's mtime stays stable and other drives read one body).
    Temp-write + atomic replace keeps concurrent readers safe."""
    body = json.dumps(inv, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    try:
        if OUT_PATH.read_text(encoding="utf-8") == body:
            return False
    except (OSError, ValueError):
        pass
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(str(tmp), str(OUT_PATH))
    return True

results = []
checks = [0, 0]


def check(name, cond, msg=""):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + str(name)
          + ("  " + str(msg) if msg else ""))


def run_child(path):
    """One child run: echoed (streamed-then-summarized) and returned as
    drivekit's parsed run dict."""
    py = str(POOL_VENV) if POOL_VENV.exists() else sys.executable
    # THE CHILD ENCODING CONTRACT (drivekit.run_child — run_fleet's own
    # convention): the child runs with PYTHONIOENCODING=utf-8 and the
    # parent decodes utf-8 with strict errors REPLACED. Without the
    # env pin, a bare -X utf8 parent would decode the child's cp1252
    # pipe bytes strictly, the reader thread would die on the first
    # em-dash, and proc.stdout would come back None — an entry that
    # only works under some consoles is not an entry.
    r = drivekit.run_child(path, TIMEOUT_S, interpreter=[py], cwd=HERE)
    print(f"\n========= {path.name} ({r['dur']:.1f} s) =========")
    if r["why"]:
        print("[timeout]", r["why"])
    # The lease, rewritten for the echo only: a child's own `RESULT:`
    # line must NOT look like this entry's fleet line — the fleet's
    # ledger takes the FIRST `RESULT:` line in a drive's output (and
    # its regex is NOT line-anchored, so even a prefix like
    # CHILD-RESULT: matches), and a raw child line would attribute
    # the child's count (15, 23, 22) to the entry instead of its sum
    # (60). Lowercase breaks the fleet's exact case-sensitive token;
    # this entry's own sum line stays the ONLY `RESULT:` in its
    # output.
    print(re.sub(r"(?m)^(RESULT:)", "child result:", r["out"].rstrip("\n")))
    if r["err"].strip():
        print("[stderr]", re.sub(r"(?m)^(RESULT:)", "child result:",
                                 r["err"].rstrip("\n")))
    return r


def grade_child(path):
    """Check, after hermetic retry, one child: returns (cls, passed, failed)."""
    r1 = run_child(path)
    cls = drivekit.grade_first(r1, scenario_rc=(2,), scenario_label="SCENARIO",
                               honest_zero_check=True)
    if cls != "FAIL":
        passed, failed = drivekit.counts(r1)
        return cls, passed, failed
    # The cooldown retry: one failed run may be a flake (drivekit's
    # rerun triangle — a passing rerun is a FLAKE, a repeating one
    # stays a REGRESSION, exactly the fleet's own triage).
    print(f"\n[retry] {path.name} failed once — cooldown rerun")

    def _green(r):
        return r["failed"] == 0 and r["rc"] == 0 and (r["passed"] or 0) > 0

    _status, _prev, r2, _n = drivekit.triage(
        r1, lambda: run_child(path), retries=1,
        cooldown_s=RERUN_COOLDOWN_S, scenario_rc=(2,),
        scenario_label="SCENARIO", honest_zero_check=True)
    passed2, failed2 = drivekit.counts(r2)
    if _status == "FLAKE":
        return "FLAKE", passed2, failed2
    return "REGRESSION", passed2, failed2


def main():
    print("== PAYOUT-CONTRACT BATTERY ==", flush=True)
    if not POOL_VENV.exists():
        print("SKIP: pool venv missing")
        print("RESULT: 0 passed, 0 failed")
        return 0

    classes, totals = {}, [0, 0]
    per_child = {}
    for name, rule in CHILDREN:
        path = HERE / name
        cls, passed, failed = grade_child(path)
        classes[name] = cls
        per_child[name] = (passed, failed)
        totals[0] += passed
        totals[1] += failed
        check(f"{name} classification for {rule}",
              cls in ("PASS", "SCENARIO", "FLAKE"), cls)

    print("\n== classification ==\n")
    for name, cls in classes.items():
        print(f"  {cls:<12} {name}")

    check("REGRESSION entry-wide (no child failed twice)",
          not any(c.startswith("REGRESSION") for c in classes.values()),
          classes)

    # THE EMISSION (docs contract): what this run verified, as JSON —
    # then the emission's own honest checks: byte-stable re-emit, and
    # a re-parse whose facts agree with the run's own grading.
    emission = emit_inventory(build_inventory(classes, per_child))
    _doc = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    check("the verified inventory is emitted as payout_contract.json",
          _doc["inventory"] == "payout_contract"
          and len(_doc["rules"]) == len(CHILDREN)
          and [r["child"] for r in _doc["rules"]]
              == [c[0] for c in CHILDREN])
    check("a re-emit of the same verification is byte-stable",
          emit_inventory(build_inventory(classes, per_child)) is False)
    check("the emitted counts agree with the run's own grading",
          all(r["classification"] == classes[r["child"]]
              and r["checks"]["passed"] == per_child[r["child"]][0]
              and r["checks"]["failed"] == per_child[r["child"]][1]
              for r in _doc["rules"])
          and _doc["totals"]["children_checks_passed"] == totals[0]
          and _doc["totals"]["children_checks_failed"] == totals[1],
          emission)

    fleet = ("RESULT: %d passed, %d failed" % (totals[0], totals[1]))
    print("\n" + fleet)
    ok = all(results) and totals[1] == 0
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
