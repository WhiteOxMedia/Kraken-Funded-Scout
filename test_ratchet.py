"""Test the ratchet trail: 3% base -> 2% at +$100 -> 1.5% at +$200.
Verifies on TAO's exact numbers from tonight."""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from background_scanner import _ratchet_trail_pct, CONTEST_TRAIL_PCT

results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))

# thresholds on a $5,000 position
check("no run yet -> base 3%", _ratchet_trail_pct(100.0, 100.0, 5000) == CONTEST_TRAIL_PCT)
check("run to +$99 -> still 3%", _ratchet_trail_pct(100.0, 101.98, 5000) == 3.0)
check("run to +$100 -> 2%", _ratchet_trail_pct(100.0, 102.0, 5000) == 2.0)
check("run to +$199 -> 2%", _ratchet_trail_pct(100.0, 103.98, 5000) == 2.0)
check("run to +$200 -> 1.5%", _ratchet_trail_pct(100.0, 104.0, 5000) == 1.5)
check("run to +$400 -> 1.5%", _ratchet_trail_pct(100.0, 108.0, 5000) == 1.5)
check("underwater -> base (no ratchet on losers)",
      _ratchet_trail_pct(100.0, 98.0, 5000) == CONTEST_TRAIL_PCT)

# TAO tonight, exact: entry 237.7169, peak 246.7438, size 5000
t = _ratchet_trail_pct(237.7169, 246.7438, 5000.0)
check("TAO tonight: +$190 run -> 2% trail", abs(t - 2.0) < 1e-9, f"got {t}")
trigger = 246.7438 * (1 - t / 100)
check("TAO's ratcheted trigger = 241.81 (vs flat 239.34 — $12 tighter)",
      abs(trigger - 241.81) < 0.05, f"got {trigger:.2f}")
check("TAO at 240.45 would have FIRED the ratcheted exit",
      240.45 <= trigger)

fails = [r for r in results if not r[1]]
out = BASE / "ratchet_check.txt"
out.write_text("\n".join(
    f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{d}]" if d and not ok else "")
    for name, ok, d in results), encoding="utf-8")
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES'}")
