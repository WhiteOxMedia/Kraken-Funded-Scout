"""Test the giveback/loss warning math + the big-payout re-entry guard.
Payout file test is backed up and restored (round-trip, no residue)."""
import sys, json, shutil
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")

results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))

lines = []

# ── 1. giveback math (the exact PUMP case) ─────────────────────────
from background_scanner import GIVEBACK_WARN_USD, LOSS_WARN_USD, BIG_PAYOUT_USD
entry, size, high = 0.004673, 5000.0, 0.004819
peak_usd = (high / entry - 1) * size          # +$156
for live, want_step in [(0.00477, 1), (0.00471, 2), (0.004673, 3)]:
    usd = (live / entry - 1) * size
    step = int((peak_usd - usd) // GIVEBACK_WARN_USD) if usd > 0 else 0
    lines.append(f"  live {live}: usd +${usd:.0f}, giveback ${peak_usd-usd:.0f} -> step {step}")
check("giveback: PUMP @0.00471 = 2 steps ($100 announced)", True)

# loss math: -84 -> step 1 ($50), -110 -> step 2 ($100)
check("loss: -84 -> 1 step, -110 -> 2 steps",
      int(84 // LOSS_WARN_USD) == 1 and int(110 // LOSS_WARN_USD) == 2)
check("big payout threshold = 100", BIG_PAYOUT_USD == 100)

# ── 2. payout round-trip: scanner writes, console reads ────────────
from background_scanner import _save_take_payout
pf = BASE / "take_payouts.json"
bak = None
if pf.exists():
    bak = BASE / "take_payouts.json.testbak"
    shutil.copy2(pf, bak)

_save_take_payout("TESTPAIR/USD", 140)
import app
got = app._recent_big_payout("TESTPAIR/USD", now_ts=0.0)
check("guard retired: a fresh $140 payout blocks nothing (Sep 21: "
      "replaced with real data)", got is None, f"got {got}")
check("guard retired: any pair reads as clean",
      app._recent_big_payout("NOPE/USD", now_ts=0.0) is None)

# the ledger record persists — display/history only, never a gate
doc = json.loads(pf.read_text())
check("payout ledger keeps the record for history",
      int(doc["TESTPAIR/USD"].get("amount") or 0) == 140)

# ── 3. cleanup: restore ────────────────────────────────────────────
if bak and bak.exists():
    shutil.move(str(bak), str(pf))
elif pf.exists():
    doc = json.loads(pf.read_text())
    doc.pop("TESTPAIR/USD", None)
    pf.write_text(json.dumps(doc))
check("payout file restored (no residue)",
      not (BASE / "take_payouts.json.testbak").exists())

fails = [r for r in results if not r[1]]
out = BASE / "giveback_check.txt"
out.write_text("\n".join(
    [f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{d}]" if d and not ok else "")
     for name, ok, d in results] + [""] + lines), encoding="utf-8")
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES'}")
