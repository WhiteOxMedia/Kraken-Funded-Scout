"""Test THE TRAIL RATCHET re-based to peak-percent thresholds (Sep 21).

The +$100/+$200 dollar thresholds were tier-blind: at the current tier
(~$400–470 entries) they demanded a +21%/+43% peak before tightening —
ZERO engagements in the 48-trade ledger (the verdict-cost study). The
re-base pins the ratchet in peak-PERCENT terms so it engages on any real
run at every tier, while `size` stays in the signature for call-site
stability. The tier-independence is itself pinned: identical peaks give
identical widths at $470 and $10,000 entries.
"""
import sys
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

results = []


def check(name, ok, detail=""):
    results.append((name, ok, str(detail)))
    print(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))


from background_scanner import _ratchet_trail_pct, CONTEST_TRAIL_PCT

# ── the re-based thresholds, boundary-exact ─────────────────────────────
check("no run yet -> base 3%", _ratchet_trail_pct(100.0, 100.0, 5000) == CONTEST_TRAIL_PCT)
check("peak +0.99% -> still 3%", _ratchet_trail_pct(100.0, 100.99, 5000) == 3.0)
check("peak +1.0% -> 2%", _ratchet_trail_pct(100.0, 101.0, 5000) == 2.0)
check("peak +1.99% -> still 2%", _ratchet_trail_pct(100.0, 101.99, 5000) == 2.0)
check("peak +2.0% -> 1.5%", _ratchet_trail_pct(100.0, 102.0, 5000) == 1.5)
check("peak +4.0% -> 1.5%", _ratchet_trail_pct(100.0, 104.0, 5000) == 1.5)
check("underwater -> base (no ratchet on losers)",
      _ratchet_trail_pct(100.0, 98.0, 5000) == CONTEST_TRAIL_PCT)
check("zero entry fails safe to base",
      _ratchet_trail_pct(0.0, 102.0, 5000) == CONTEST_TRAIL_PCT)

# ── tier independence: percent terms cannot drift with the tier ─────────
check("same peak, $470 entry -> 1.5%",
      _ratchet_trail_pct(2771.17, 2771.17 * 1.021, 470.0) == 1.5)
check("same peak, $10,000 entry -> 1.5%",
      _ratchet_trail_pct(100.0, 102.0, 10000.0) == 1.5)
check("same peak, $400 entry -> 2%",
      _ratchet_trail_pct(0.187, 0.187 * 1.011, 400.0) == 2.0)
check("same peak, $10,000 entry -> 2%",
      _ratchet_trail_pct(100.0, 101.0, 10000.0) == 2.0)

# ── the real trades, recomputed under the new policy ────────────────────
# TAO (the Aug 26 lesson, exact numbers from the old suite): peak +3.8%
t = _ratchet_trail_pct(237.7169, 246.7438, 5000.0)
check("TAO: +3.8% peak -> 1.5% (the tightest protection)", abs(t - 1.5) < 1e-9, f"got {t}")
trigger = 246.7438 * (1 - t / 100)
check("TAO's ratcheted trigger = 243.04",
      abs(trigger - 243.04) < 0.05, f"got {trigger:.2f}")
check("TAO at 240.45 still FIRES the ratcheted exit", 240.45 <= trigger)

# ETH (today, $470 entry): the case that never engaged under +$100/+$200
t = _ratchet_trail_pct(2771.17, 2800.0, 470.25450947907643)
check("ETH today: +1.04% peak -> 2% (engages at this tier now)",
      abs(t - 2.0) < 1e-9, f"got {t}")
t = _ratchet_trail_pct(0.187, 0.1931, 400.62319829699635)
check("FARTCOIN today: +3.3% peak -> 1.5%",
      abs(t - 1.5) < 1e-9, f"got {t}")

# ── the engagement proof, straight from the verdict-cost study ──────────
import json
import sqlite3

# clock-audit: ok — LIVE-PROBE (manual bot-day report — reports on the live system by design) (clockbombs census 4)
conn = sqlite3.connect(BASE / "ohlcv_cache.db")
engaged = 0
checked = 0
# clock-audit: ok — LIVE-PROBE (manual bot-day report — reports on the live system by design) (clockbombs census 4)
for line in (BASE / "contest_trades.jsonl").read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    r = json.loads(line)
    entry, size = float(r["entry"]), float(r["size"])
    try:
        ts = r["sold_at"]
        import datetime as _dt
        sold = _dt.datetime.fromisoformat(ts)
        ex_ms = int(sold.timestamp() * 1000)
    except Exception:
        continue
    import datetime as _dt2
    from_ts = ex_ms - 2 * 60 * 60 * 1000
    hi = conn.execute(
        "SELECT MAX(high) FROM ohlcv WHERE pair=? AND ts>=? AND ts<=?",
        (r["pair"], from_ts, ex_ms + 240 * 60 * 1000)).fetchone()[0]
    if not hi:
        continue
    checked += 1
    if _ratchet_trail_pct(entry, hi, size) != CONTEST_TRAIL_PCT:
        engaged += 1
check("the re-based ratchet engages on the real ledger",
      engaged > 0, f"{engaged}/{checked} candle-graded trades tighten")

fails = [r for r in results if not r[1]]
out = BASE / "ratchet_check.txt"
out.write_text("\n".join(
    f"{'PASS' if ok else 'FAIL'}  {name}  {detail}" for name, ok, detail in results)
    + f"\nRESULT: {len(results) - len(fails)}/{len(results)}\n")
print(f"RESULT: {len(results) - len(fails)}/{len(results)} passed -> {out.name}")
sys.exit(1 if fails else 0)
