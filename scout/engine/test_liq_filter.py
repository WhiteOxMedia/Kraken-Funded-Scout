"""Test the rotation-aware liquidity filter against real engine data."""
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")
now = datetime.now(MST)
lines = [f"now: {now.strftime('%a %I:%M %p')} — testing filter_illiquid"]

from background_scanner import filter_illiquid, rotation_key_for_hour, _schedule_for_dow
rot = rotation_key_for_hour(now.hour)
sched = _schedule_for_dow(now.weekday())
lines.append(f"current rotation: {rot} ({sched})")

def fake_df(daily_usd, price=1.0):
    # 24 hourly candles each volume*price = daily/24
    vol = daily_usd / 24.0 / price
    idx = pd.date_range("2026-01-01", periods=30, freq="1h")
    return pd.DataFrame({"volume": [vol] * 30, "close": [price] * 30}, index=idx)

results = []


def run(name, pairs_vols, expect_in, expect_out):
    data = {p: fake_df(v) for p, v in pairs_vols.items()}
    kept, excluded = filter_illiquid(data, 750_000)
    ok = set(kept.keys()) == set(expect_in) and {p for p, _ in excluded} == set(expect_out)
    results.append(ok)
    lines.append(f"{'PASS' if ok else 'FAIL'}  {name}: kept={sorted(kept.keys())} "
                 f"excluded={sorted(p for p, _ in excluded)}")


# against the LIVE engine cells for the current rotation
import json
import scratch_space   # scratch policy: nothing volatile lands in the repo root
# clock-audit: ok — LIVE-PROBE (manual bot-day report — reports on the live system by design) (clockbombs census 4)
doc = json.loads((BASE / "rotation_thresholds.json").read_text())
uni = doc.get("universe", {})

def cell_vol(pair):
    c = uni.get(pair, {}).get(sched, {}).get(rot) or {}
    return float(c.get("median_volume_usd") or 0)

# ALGO: rotation median should be high (60-103k) → passes with healthy daily
# S: rotation median ~3k → fails rotation floor regardless of daily
lines.append("")
lines.append(f"engine cells right now: ALGO={cell_vol('ALGO/USD')/1000:.0f}k "
             f"DOT={cell_vol('DOT/USD')/1000:.0f}k S={cell_vol('S/USD')/1000:.0f}k "
             f"PNUT={cell_vol('PNUT/USD')/1000:.0f}k BTC={cell_vol('BTC/USD')/1000:.0f}k")

# case 1: active coin, healthy daily → in. dead coin, any daily → out
run("active in / dead out",
    {"ALGO/USD": 600_000, "S/USD": 600_000, "BTC/USD": 5_000_000},
    expect_in=[p for p in ["ALGO/USD", "BTC/USD"]
               if cell_vol(p) >= (10_000 if sched == "weekend" else 15_000)],
    expect_out=[p for p in ["ALGO/USD", "S/USD", "BTC/USD"]
                if p not in [q for q in ["ALGO/USD", "BTC/USD"]
                             if cell_vol(q) >= (10_000 if sched == "weekend" else 15_000)]])

# case 2: unknown coin (not in engine) → daily floor decides
run("unknown coin: daily floor only",
    {"NEWCOIN/USD": 800_000, "DEADNEW/USD": 100_000},
    expect_in=["NEWCOIN/USD"], expect_out=["DEADNEW/USD"])

# case 3: known coin died recently — fat history but $150k daily → out (backstop)
run("recently-died coin blocked by daily backstop",
    {"ALGO/USD": 150_000},
    expect_in=[], expect_out=["ALGO/USD"])

out = BASE / "liq_filter_check.txt"
scratch_space.report_temp('liq_filter_check.txt').write_text("\n".join(lines), encoding="utf-8")
fails = results.count(False)
print(f"RESULT: {len(results) - fails} passed, {fails} failed")


def main():
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    sys.exit(main())
