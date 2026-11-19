"""THE LIQUID UNIVERSE — the rule the book now runs BEFORE a signal exists.

The book kept 4.9% of its signals and threw the rest away at entry because the
order did not fit 1% of the pair's median hourly dollar volume. Wiring the rule
into eligibility is only honest if the arithmetic, the direction of the gate and
the two live call sites are all pinned.

Prints "RESULT: N passed, M failed".
"""
import numpy as np
import pandas as pd

import liquid_universe as lu

results = []


def check(name, ok, why=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}"
          + (f"  [{why}]" if why and not ok else ""))


def frame(hourly_usd, bars=48):
    """Synthetic hourly candles whose close x volume is `hourly_usd`."""
    idx = pd.date_range("2026-11-01", periods=bars, freq="h", tz="UTC")
    px = np.full(bars, 2.0)
    vol = np.full(bars, float(hourly_usd) / 2.0)
    return pd.DataFrame({"open": px, "high": px, "low": px, "close": px,
                         "volume": vol}, index=idx)


print("== 1. the rule and its arithmetic ==")
check("1.1 order is the shipped $2,400", lu.ORDER_USD == 2400.0, lu.ORDER_USD)
check("1.2 participation is the shipped 1%", lu.PARTICIPATION == 0.01,
      lu.PARTICIPATION)
thick = frame(400_000)      # 1% = $4,000
thin = frame(100_000)       # 1% = $1,000
ok_t, ev_t = lu.check(thick)
ok_n, ev_n = lu.check(thin)
check("1.3 a pair whose 1% clears the order is eligible", ok_t, ev_t)
check("1.4 capacity IS 1% of median hourly dollars",
      abs(ev_t["capacity_usd"] - 4_000.0) < 0.01, ev_t["capacity_usd"])
check("1.5 a pair whose 1% does not clear the order is NOT eligible", ok_n is False,
      ev_n)
check("1.6 the refusal carries the numbers that decided it",
      ev_n["capacity_usd"] == 1_000.0 and ev_n["fill_pct"] == 240.0, ev_n)

print("== 2. the boundary is the order, not something softer ==")
edge_in = frame(240_000)    # 1% = exactly $2,400
edge_out = frame(239_000)
check("2.1 exactly 1% clears (<=, not <)", lu.eligible(edge_in) is True)
check("2.2 one dollar under does not", lu.eligible(edge_out) is False)
check("2.3 a 30 percent order excludes even BTC-scale volume only at "
      "$30k/h*100 = $3M/h", lu.eligible(frame(3_000_000), order_usd=30_000.0) is True
      and lu.eligible(frame(300_000), order_usd=30_000.0) is False)

print("== 3. unmeasurable is not eligible ==")
check("3.1 too few bars cannot be measured", lu.median_hourly_usd(frame(1e9, bars=5)) is None)
check("3.2 and therefore is not eligible", lu.eligible(frame(1e9, bars=5)) is False)
check("3.3 a frame without volume is refused, not crashed",
      lu.eligible(pd.DataFrame({"close": [1.0] * 48})) is False)

print("== 4. the filter splits a fetched universe and says why ==")
data = {"BTC/USD": frame(3_000_000), "ETH/USD": frame(1_200_000),
        "DOGE/USD": frame(100_000), "WIF/USD": frame(40_000)}
kept, out = lu.filter_universe(data)
check("4.1 only the fillable pairs are kept",
      sorted(kept) == ["BTC/USD", "ETH/USD"], sorted(kept))
check("4.2 every dropped pair carries its capacity", len(out) == 2
      and all(r["capacity_usd"] > 0 for r in out), out)
check("4.3 the drops are ordered thinnest first", out[0]["pair"] == "WIF/USD", out[0])
u = lu.universe(data)
check("4.4 the record counts are consistent",
      u["pairs_in"] == 4 and u["pairs_fillable"] == 2
      and sorted(u["kept"]) == ["BTC/USD", "ETH/USD"], u)

print("== 5. the wiring: judged before the bracket, config-driven ==")
import config
cfg = config.TradingConfig()
check("5.1 ships ON", getattr(cfg, "liquid_universe_enabled", None) is True,
      getattr(cfg, "liquid_universe_enabled", None))
check("5.2 ships at the $2,400 order",
      float(getattr(cfg, "liquid_universe_order_usd", 0)) == 2400.0,
      getattr(cfg, "liquid_universe_order_usd", None))

src = open("scanner.py", encoding="utf-8").read()
check("5.3 both per-pair evaluations run the gate",
      src.count("liquid_universe_enabled") == 2, src.count("liquid_universe_enabled"))
check("5.4 an unfillable pair is demoted to hold with the reason",
      src.count("order is more than 1% of") == 2
      or src.count("order is more than") == 2, src.count("order is more than"))
check("5.5 the score is carried on the ScanResult",
      "entry_capacity" in src)

bg = open("background_scanner.py", encoding="utf-8").read()
check("5.6 the running loop filters the universe before the bracket",
      bg.find("_liquid_universe.universe(all_data") < bg.find("_cyc = crown_cycle(exchange, results, all_data")
      if "_liquid_universe.universe(all_data" in bg else False,
      bg.find("_liquid_universe.universe(all_data"))
check("5.7 and logs what it dropped instead of shrinking silently",
      "can fill a" in bg)

print(f"RESULT: {sum(results)} passed, {len(results) - sum(results)} failed")
raise SystemExit(0 if all(results) else 1)
