"""Test the QC's LONE WOLF awareness (user rule, Aug 31: "the QC verdict
needs to match lone-wolf alerts — information is key, don't ever keep her
in the dark"). The QC shows the same lone-wolf context the alert funnel
uses and names near-misses. The conviction floor stays 60 — that IS the
match: every alert speaks the DEEP number (>=60 required), so the QC's
deep floor is the same line the alert already passed."""
import sys
from pathlib import Path

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
check("near-miss verdict names the shape (toast)",
      "LONE WOLF NEAR MISS (" in src)
check("near-miss verdict speaks it", "Lone wolf near miss!" in src)
check("NO GREEN line distinguishes a lone-wolf near miss",
      "NO GREEN — LONE WOLF NEAR MISS" in src)

# ── 3. the MATCH invariant: the QC floor stays 60 ─────────────────────
check("QC floor is still 60 (the line every alert already passed)",
      "QC_SCALP_FLOOR = 60" in src)
check("no lone-wolf discount applied to QC_SCALP_FLOOR itself",
      "QC_SCALP_FLOOR -" not in src and "QC_SCALP_FLOOR-" not in src)

# ── 4. the other half of matching: alerts speak the DEEP number ──────
bsrc = open("background_scanner.py", encoding="utf-8").read()
check("scanner's desktop alert still requires deep >= 60 (same line as the QC)",
      "_deep.confidence < 60" in bsrc)
check("alert title speaks the deep number",
      "_dc = float(_deep.confidence) if _deep is not None" in bsrc)
check("lone-wolf discount exists in the scanner (the shared test)",
      "def lone_wolf_discount" in bsrc)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_qclonewolf_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
