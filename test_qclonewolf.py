"""Test the QC's LONE WOLF awareness (user rule, Aug 31: "the QC verdict
needs to match lone-wolf alerts — information is key, don't ever keep her
in the dark"). The QC shows the same lone-wolf context the alert funnel
uses and names near-misses. The conviction floor stays 60 — that IS the
match: every alert speaks the DEEP number (>=60 required), so the QC's
deep floor is the same line the alert already passed."""
import sys
from pathlib import Path
import scratch_space   # scratch policy: nothing volatile lands in the repo root

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


src = open("app.py", encoding="utf-8").read()

# ── 1. the QC computes the SAME lone-wolf test the alert funnel uses ──
check("QC calls the scanner's lone_wolf_discount (identical test + constants)",
      "_bs6.lone_wolf_discount(kraken, pair)" in src)
check("dup-safe reload for older console processes",
      'hasattr(_bs6, "lone_wolf_discount")' in src)
check("result stores lone_wolf_pts", '"lone_wolf_pts"' in src)
check("result stores lone_wolf_note", '"lone_wolf_note"' in src)

# ── 2. the QC SHOWS it ────────────────────────────────────────────────
check("lone-wolf LED in the status row", "🐺 LONE WOLF — {lw_note}" in src)
# (Sep 5, ADVISORY BRAIN: the near-miss verdicts retired with the
# conviction floor — there is no number line to be "near" anymore. The
# lone-wolf CONTEXT still displays; the verdict names the scan's
# DIRECTION, never a number.)
check("the near-miss verdicts are retired with the floor",
      "LONE WOLF NEAR MISS (" not in src
      and "Lone wolf near miss!" not in src
      and "NO GREEN — LONE WOLF NEAR MISS" not in src)
check("the verdict names the scan's direction (the number never reds)",
      "THE SCAN READS" in src)

# ── 3. the MATCH invariant: the QC mirrors the alert path ────────────
check("QC_SCALP_FLOOR stays defined (history queries + context)",
      "QC_SCALP_FLOOR = 60" in src)
check("the QC never blocks on the number (the advisory mirror)",
      "sig.confidence >= QC_SCALP_FLOOR" not in src)
check("no lone-wolf discount applied to QC_SCALP_FLOOR itself",
      "QC_SCALP_FLOOR -" not in src and "QC_SCALP_FLOOR-" not in src)

# ── 4. the other half of matching: alerts speak the DEEP number ──────
bsrc = open("background_scanner.py", encoding="utf-8").read()
check("scanner's desktop alert gates the scan's DIRECTION (the number sizes "
      "— same line as the QC, Sep 5)",
      "_deep.action != \"buy\"" in bsrc
      and "_deep.confidence < 60" not in bsrc)
check("alert title speaks the deep number",
      "_dc = float(_deep.confidence) if _deep is not None" in bsrc)
check("lone-wolf discount exists in the scanner (the shared test)",
      "def lone_wolf_discount" in bsrc)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open(scratch_space.report_temp('_qclonewolf_check.txt'), "w", encoding="utf-8").write("\n".join(lines))

print(f"RESULT: {PASS} passed, {FAIL} failed")

raise SystemExit(1 if FAIL else 0)   # the fleet-honesty fix (Sep 30): DONE-only tails failed closed
print("DONE")
