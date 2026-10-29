"""test_payout_contract_docs.py — THE PAYOUT-CONTRACT DOCS DRIVE (Oct 28).

The battery entry (test_payout_contract_battery.py) emits what it just
verified as payout_contract.json: the three contract rules, the drive
that pins each, the live classification and check counts, and the
exact facts the README's contract section must keep documented. This
drive grades the README against that artifact — and fails the moment
the section drifts from what the pool's verification says.

Drift surfaces it pins, check by check:

  1. THE ARTIFACT: present, parseable, the inventory schema, three
     rules, each graded green by the battery's last run.
  2. THE SECTION: present at the documented heading, and every rule's
     readme_facts — title tokens, code names, audit actions, pin
     drive — still documented verbatim.
  3. THE NUMBERS: the section's checks-count line agrees with the
     inventory's live per-child counts, and the envelope entry is
     still credited.
  4. HERMETIC: the drive only ever reads — both inputs are
     byte-identical across the run.

RECOVERY when it fails: run the battery. If the entry is green, its
emission is the contract — bring the README section back to what the
inventory documents. If the entry is red, fix the code first; a docs
drive never talks over a failed verification.

House conventions: check(n, cond, why), the fleet line, sys.exit.
Like the battery, the drive reconfigures stdout to UTF-8 before any
Unicode fact can appear on a cp1252 console.
"""
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
README = HERE / "README.md"
INVENTORY = HERE / "payout_contract.json"

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    # The drift explanation belongs on a FAIL row only: a green row
    # carries the contract, not its failure mode.
    print(("PASS  " if cond else "FAIL  ") + str(n)
          + (("  " + why) if (why and not cond) else ""))


def _stamp(path):
    """(mtime ns, size) or None — the hermetic no-write witness."""
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


_before = {p.name: _stamp(p) for p in (README, INVENTORY)}

# ── 1. THE ARTIFACT ──────────────────────────────────────────────────
inv = None
try:
    inv = json.loads(INVENTORY.read_text(encoding="utf-8"))
except Exception as e:                      # missing / corrupt inventory
    check("1.1 the emitted inventory is present and parseable", False,
          f"{INVENTORY.name}: {e} — run the battery to emit it")

rules = (inv or {}).get("rules") or []
totals = (inv or {}).get("totals") or {}
if inv is not None:
    check("1.1 the emitted inventory is present and parseable",
          inv.get("inventory") == "payout_contract"
          and inv.get("generated_by") == "test_payout_contract_battery.py",
          "not the payout-contract inventory")
    check("1.2 the inventory carries three rules, graded green by the "
          "battery's last run",
          totals.get("rules") == len(rules) == 3
          and all(r.get("classification") in ("PASS", "SCENARIO", "FLAKE")
                  and r.get("checks", {}).get("failed") == 0
                  and r.get("checks", {}).get("passed", 0) > 0
                  for r in rules),
          str([(r.get("rule"), r.get("classification"), r.get("checks"))
                for r in rules]))

# ── 2. THE SECTION ──────────────────────────────────────────────────
section = None
try:
    readme = README.read_text(encoding="utf-8")
except Exception as e:                       # README unreadable
    check("2.1 the README section exists at the documented heading",
          False, f"{README.name}: {e}")
else:
    _m = re.search(
        r"(?ms)^##\s+" + re.escape((inv or {}).get("readme", {})
                                    .get("section", "\x00")) + r".*?(?=^##\s|\Z)",
        readme)
    check("2.1 the README section exists at the documented heading",
          bool(_m) and bool(inv),
          "the heading the inventory documents is gone — the section "
          "drifted off the contract")
    section = _m.group(0) if _m else ""
    low = section.lower()

    for _r in rules:
        facts = _r.get("readme_facts") or []
        missing = [f for f in facts if f.lower() not in low]
        check(f"2.{_r.get('rule')}.1 rule {_r.get('rule')}: every fact "
              "the inventory demands stays documented",
              not missing,
              f"README drifted from the verified inventory; missing "
              f"tokens: {missing}")
        check(f"2.{_r.get('rule')}.2 rule {_r.get('rule')}: the drive "
              "that pins it is still credited",
              (_r.get("child") or "\x00") in section,
              f"pin drive missing from the section")

    for _t in (inv or {}).get("read_together") or []:
        check(f"2.4 read-together token documented: {_t}",
              _t.lower() in low,
              "the envelope entry or a cross-reference it requires "
              "drifted out of the section")

# ── 3. THE NUMBERS ──────────────────────────────────────────────────
if rules and section is not None:
    _sum = totals.get("children_checks_passed")
    _counts = " + ".join(str(r.get("checks", {}).get("passed", 0))
                         for r in rules)
    _line = f"{_counts} = {_sum} checks"
    check("3.1 the section's checks-count line agrees with the "
          "inventory's live counts",
          _line in section,
          f"expected a line carrying {_line!r}; the counts drifted")
    check("3.2 the counts line adds up",
          isinstance(_sum, int)
          and sum(r.get("checks", {}).get("passed", 0) for r in rules) == _sum
          and totals.get("children_checks_failed", 1) == 0,
          str(totals))

# ── 4. HERMETIC ─────────────────────────────────────────────────────
_after = {p.name: _stamp(p) for p in (README, INVENTORY)}
check("4.1 the drive only ever reads — both inputs byte-identical "
      "across the run",
      _after == _before, str({"before": _before, "after": _after}))

print("---")
print("RESULT: %d passed, %d failed" % (sum(results), results.count(False)))
sys.exit(0 if all(results) else 1)
