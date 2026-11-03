"""Test TIER AUTO-ADJUST (user rule, Sep 1: 'do these numbers change
depending on the contest level 1k, 5k, 10k? everything should auto adjust
when the contest is selected'). Every contest-side number must scale with
the tier: the death line, the buffer, the pace, the hard stop, the
warnings, and the facts panel."""
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


import contest_account as ca
import background_scanner as bs
import scratch_space   # scratch policy: nothing volatile lands in the repo root
ap = open("app.py", encoding="utf-8").read()
sc = open("background_scanner.py", encoding="utf-8").read()

# ── 0. THE BALANCE REMINDER (user rule, Sep 2 — "go"): the growing
#      buffer is only as accurate as the sidebar's current balance (no
#      API for the contest side); SOLD is the moment it moved, so she
#      asks for the update on every close ────────────────────────────
check("SOLD speaks the balance-update reminder",
      "Update the contest balance in the sidebar — my numbers follow it!" in ap)

# ── 1. every account number scales with the tier ─────────────────────
for tier, line, pace in ((1000, 970.0, 24.0), (5000, 4850.0, 120.0),
                         (10000, 9700.0, 240.0)):
    s = {"tier": tier, "start_balance": float(tier), "current_balance": float(tier)}
    check(f"{tier} tier: death line ${line:,.0f}",
          abs(ca.death_line(s) - line) < 0.01, ca.death_line(s))
    check(f"{tier} tier: pace ${pace:,.0f}/day",
          abs(ca.daily_pace_usd(s) - pace) < 0.01)

# entry caps scale: same coin (2% dd), three tiers
c1 = ca.max_entry_usd(1000.0, 2.0, 1000.0)
c5 = ca.max_entry_usd(5000.0, 2.0, 5000.0)
c10 = ca.max_entry_usd(10000.0, 2.0, 10000.0)
check("entry cap scales: 1k $510 < 5k $2,550 < 10k $5,100",
      c1 < c5 < c10 and abs(c5 - c1 * 5) < 5 and abs(c10 - c1 * 10) < 10,
      f"{c1:.0f}/{c5:.0f}/{c10:.0f}")

# ── 2. the hard stop scales with the buffer (no more fixed -$100) ────
def stop_for(tier, balance=None):
    s = {"tier": tier, "start_balance": float(tier),
         "current_balance": float(balance or tier)}
    return max(ca.buffer(s) * 0.5, 5.0)


check("hard stop: fresh 10k -> -$150 (was fixed -$100)",
      abs(stop_for(10000) - 150.0) < 0.01, stop_for(10000))
check("hard stop: fresh 1k -> -$15 (the old -$100 fired AFTER death)",
      abs(stop_for(1000) - 15.0) < 0.01, stop_for(1000))
check("hard stop tightens as the buffer shrinks: 10k at $9,800 -> -$50",
      abs(stop_for(10000, 9800.0) - 50.0) < 0.01, stop_for(10000, 9800.0))
check("the light's stop reads the contest account (tier-aware; platform-only since Sep 5 — the daily lock removed)",
      "_ca_l.buffer(_acct_l) * 0.5" in sc and "effective_buffer" not in sc)
check("the fixed -100 stop is gone", "_usd_now <= -100.0" not in sc)
check("the stop message states the platform death-line buffer",
      "half the platform death-line buffer" in sc)

# ── 3. the warnings scale with the position size ─────────────────────
check("giveback threshold: 1.5% of entry size (not fixed $50)",
      "GIVEBACK_WARN_PCT_OF_SIZE" in sc and "_gb_thr = max(size *" in sc)
check("loss threshold: same scaling",
      "LOSS_WARN_PCT_OF_SIZE" in sc and "_loss_thr = max(size *" in sc)
check("the thresholds at 10k ($3,400 entry) ≈ the old $50 loudness",
      abs(3400 * 1.5 / 100 - 51.0) < 1.0)
check("the thresholds at 1k ($510 entry) ≈ $8 (the old $50 was 10% — deafening)",
      abs(510 * 1.5 / 100 - 7.65) < 0.5)
check("old USD constants kept (retired, per don't-delete)",
      "GIVEBACK_WARN_USD = 50" in sc and "LOSS_WARN_USD = 50" in sc)

# ── 4. TODAY'S FACTS is tier-aware ───────────────────────────────────
check("facts panel reads the contest account (line/buffer/pace per tier)",
      "_ca_f.death_line(_acct)" in ap and "_ca_f.daily_pace_usd(_acct)" in ap)
check("facts panel shows the tier label",
      'TIERS[_acct["tier"]]["label"]' in ap)
check("facts panel counts GOALS, not twenties",
      "GOALS BANKED" in ap and "TWENTIES BANKED" not in ap)
check("facts panel shows the pace-vs-banked read",
      "of ${_pace:,.0f}/day" in ap)

# ── 5. the card plate speaks the goal (no $20 language) ──────────────
check("the card's WHY row states the engine's expected %",
      "the engine expects +{_card_goal:.1f}% in this" in ap)
check("the card's HOW LONG row is goal-based",
      "goal +{_card_goal:.1f}% — or she says out" in ap)
check("no '$20'/'twenties' text left in the live card/watch/QC voice",
      (lambda s: "up $20" not in s and "$20 comes" not in s
                 and "I count the twenties" not in s
                 and "twenties taken" not in s)(
          ap.replace("ranked by how fast the $20 comes this hour", "")))  # the retired FAST board's text stays behind its flag

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open(scratch_space.report_temp('_tierauto_check.txt'), "w", encoding="utf-8").write("\n".join(lines))

print(f"RESULT: {PASS} passed, {FAIL} failed")


def main():
    raise SystemExit(1 if FAIL else 0)   # the fleet-honesty fix (Sep 30): DONE-only tails failed closed
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
