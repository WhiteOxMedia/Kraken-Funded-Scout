"""Test _trend_state: UP / BASING / FALLING classification.
The PENGU knife blocks, the PUMP base-catch passes."""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import pandas as pd
import app
from config import TradingConfig
import scratch_space   # scratch policy: nothing volatile lands in the repo root

cfg = TradingConfig()
results = []

def check(name, ok, detail=""):
    results.append((name, ok, detail))

def mk_df(closes):
    n = len(closes)
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-08-25", periods=n, freq="1h"),
        "open": closes, "high": [c * 1.001 for c in closes],
        "low": [c * 0.999 for c in closes],
        "close": closes,
        "volume": [1000.0] * n,
    }).set_index("timestamp")

# 1. confirmed downtrend, price at the lows -> FALLING (the PENGU knife)
fall = mk_df([1.00 * (0.995 ** i) for i in range(60)])
check("falling series -> FALLING", app._trend_state(fall, cfg, float(fall['close'].iloc[-1])) == "FALLING")

# 2. steady uptrend -> UP
rise = mk_df([1.00 * (1.004 ** i) for i in range(60)])
check("rising series -> UP", app._trend_state(rise, cfg, float(rise['close'].iloc[-1])) == "UP")

# 3. long decline, then flat base with price back over the long EMA -> BASING
#    (the PUMP-at-the-bottom entry: short EMA still under, price recovering)
base = [1.00 * (0.995 ** i) for i in range(50)] + [0.78, 0.785, 0.79, 0.795, 0.80, 0.805, 0.81, 0.815, 0.82, 0.825]
basing = mk_df(base)
state = app._trend_state(basing, cfg, 0.83)
check("decline then base, price over long EMA -> BASING (not FALLING)", state == "BASING", state)

# 4. fail-open: junk df -> UP (never crash the scan)
check("junk df -> fail-open UP", app._trend_state(pd.DataFrame(), cfg, 1.0) == "UP")

fails = [r for r in results if not r[1]]
out = BASE / "trend_check.txt"
scratch_space.report_temp('trend_check.txt').write_text("\n".join(
    f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{d}]" if d and not ok else "")
    for name, ok, d in results), encoding="utf-8")
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES'}")


def main():
    raise SystemExit(1 if fails else 0)   # the fleet-honesty tail (Sep 30 triage)


if __name__ == "__main__":
    sys.exit(main())
