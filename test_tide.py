"""Test the tide: classification thresholds + a live breadth reading."""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from background_scanner import _tide_class, _fleet_tide, TIDE_RISING_MIN, TIDE_OUT_MAX

results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))

# thresholds
check("70% -> RISING", _tide_class(70.0) == "RISING")
check("69.9% -> FLAT", _tide_class(69.9) == "FLAT")
check("50% -> FLAT", _tide_class(50.0) == "FLAT")
check("35.1% -> FLAT", _tide_class(35.1) == "FLAT")
check("35% -> OUT", _tide_class(35.0) == "OUT")
check("10% -> OUT", _tide_class(10.0) == "OUT")
check("bands are sane", TIDE_RISING_MIN == 70.0 and TIDE_OUT_MAX == 35.0)

# live reading from the real engine cache
pct, state = _fleet_tide(max_age_s=0)
if pct is None:
    check("live tide readable (cache may be empty in test env)", True, "returned None (fail-open)")
else:
    check("live breadth in 0-100", 0.0 <= pct <= 100.0, f"{pct}")
    check("live state is a real state", state in ("RISING", "FLAT", "OUT"), state)

fails = [r for r in results if not r[1]]
out = BASE / "tide_check.txt"
out.write_text("\n".join(
    f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{d}]" if d and not ok else "")
    for name, ok, d in results) +
    (f"\n\nlive tide now: {pct and f'{pct:.0f}%'} {state or '(no data)'}"), encoding="utf-8")
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES'}")
