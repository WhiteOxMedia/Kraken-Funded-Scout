"""WHAT A COIN CAN CARRY, AS A DECISION (Nov 15).

The champion question: should the bracket prefer the coin that could support the
highest spend? The machinery has to exist before any answer is believable, and
it has to be inert until the data earns it:

  1. the flags ship in the safe state (deep read ON, spend ranking OFF)
  2. the deep read really is deeper: own history, empirical drawdown, market,
     and the dollar capacity the guard would allow — each with its source
  3. the verdict is pure and says prefer / hold / no_spend / insufficient
  4. apply() honours the flag and nothing else
  5. the tournament records both the read and the decision on every fire
  6. the fire stamps them on the watch row and the journal carries them

Prints "RESULT: N passed, M failed".
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import background_scanner as bs
import config
import contest_account as ca
import scanner as sc
import spend_capacity as sp

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


print("== 1. the flags")
cfg = config.TradingConfig()
check("1.1 the deep read ships ON (the tournament gets real history)",
      cfg.deep_scan_enabled is True and cfg.deep_lookback_candles >= 1000,
      (cfg.deep_scan_enabled, cfg.deep_lookback_candles))
check("1.2 the spend ranking ships OFF",
      getattr(cfg, "spend_rank", None) is False, cfg.spend_rank)
check("1.3 the knobs exist", cfg.spend_rank_window > 0 and cfg.spend_rank_min_usd > 0,
      (cfg.spend_rank_window, cfg.spend_rank_min_usd))

print("== 2. the deep read")


class FakeKraken:
    """One deep fetch of 600 hourly bars with a known worst 4h drop."""

    def __init__(self, dd_pct=6.0):
        self.dd_pct = dd_pct
        self.calls = []

    def fetch_ohlcv(self, pair, interval_minutes=60, limit=200):
        n = min(int(limit), 600)
        closes = [100.0] * n
        # a clean peak-to-trough: 100 -> 100*(1-dd)
        peak_at = n // 2
        for i in range(n):
            if i < peak_at:
                closes[i] = 100.0
            elif i < peak_at + 4:
                closes[i] = 100.0 * (1 - self.dd_pct / 100.0 * (i - peak_at + 1) / 4)
            else:
                closes[i] = 100.0 * (1 - self.dd_pct / 100.0)
        self.calls.append((pair, interval_minutes, limit))
        return [[i * 3_600_000, c, c, c, c, 10.0] for i, c in enumerate(closes)]

    def fetch_ticker(self, pair):
        return {"bid": 99.9, "ask": 100.1, "last": 100.0,
                "high": 105.0, "low": 95.0, "quoteVolume": 750_000.0}


_orig_ctx = sp._account_ctx
sp._account_ctx = lambda: (1000.0, 1000.0)
_orig_prof = sp.profile_dd_pct
# the guard prices the PROFILE, so the test makes the two coins differ there
_PROFILE = {"AAA/USD": 2.0, "BBB/USD": 1.0}
sp.profile_dd_pct = lambda pair: (_PROFILE.get(pair, 2.0), "test_profile")
try:
    k = FakeKraken(dd_pct=6.0)
    d = sp.deep_scan(k, "AAA/USD", config.TradingConfig())
    check("2.1 it fetches the coin's own deep history, not the quick window",
          k.calls and k.calls[0][1] == 60 and k.calls[0][2] >= 1000, k.calls[:1])
    check("2.2 the empirical drawdown is measured from that history",
          d.get("dd_4h_pct") is not None and abs(d["dd_4h_pct"] - 6.0) < 0.6,
          d.get("dd_4h_pct"))
    check("2.3 the market read is present (range, position, volume, spread)",
          d.get("day_range_pct") and d.get("pos_in_day_pct") == 50.0
          and d.get("volume_24h_usd") == 750000 and d.get("spread_pct") is not None,
          {kk: d.get(kk) for kk in ("day_range_pct", "pos_in_day_pct",
                                    "volume_24h_usd", "spread_pct")})
    check("2.4 the guard's number is the profile's (one number, no silent "
          "repricing), and the deep history is reported beside it",
          d["dd_pct"] == 2.0 and d["dd_src"] == "test_profile"
          and d["dd_empirical_pct"] == 6.0, (d["dd_pct"], d.get("dd_empirical_pct")))
    _expect = ca.max_entry_usd(1000.0, 2.0, 1000.0)
    check("2.5 the capacity is the guard's own number, in dollars",
          abs(d["cap_usd"] - round(_expect, 2)) < 0.01, (d["cap_usd"], _expect))
    check("2.5b the conservative deep-history cap is priced separately, never "
          "substituted",
          abs(d["cap_deep_usd"] - round(ca.max_entry_usd(1000.0, 6.0, 1000.0), 2)) < 0.01
          and d["cap_deep_usd"] < d["cap_usd"], (d["cap_deep_usd"], d["cap_usd"]))
    check("2.6 the sources are named, never implied",
          "deep_history" in d["src"] and "ticker" in d["src"], d["src"])

    k2 = FakeKraken(dd_pct=1.0)
    d2 = sp.deep_scan(k2, "BBB/USD", config.TradingConfig())
    check("2.7 a quieter coin carries strictly more spend than a wilder one",
          d2["cap_usd"] > d["cap_usd"], (d2["cap_usd"], d["cap_usd"]))
    check("2.8 a broken fetch still returns a priced read, not an exception",
          isinstance(sp.deep_scan(None, "CCC/USD", config.TradingConfig()), dict))
finally:
    sp._account_ctx = _orig_ctx
    sp.profile_dd_pct = _orig_prof

print("== 3. the verdict")


def result(pair, cap, conf=60.0, action="buy"):
    r = sc.ScanResult(pair=pair,
                      signal=sc.Signal(action=action, confidence=conf, price=1.0,
                                       reasons=["r"]))
    r.spend = {"cap_usd": cap, "dd_pct": 3.0}
    return r


big, small, other = result("BIG/USD", 4000.0, 58.0), result("SM/USD", 300.0, 60.0), result("OTH/USD", 900.0, 60.0)
pool = {r.pair: r for r in (big, small, other)}
v = sp.verdict(small, pool, False)
check("3.1 a bigger-carry equal says 'prefer', unapplied while off",
      v["would"] == "prefer" and v["to"] == "BIG/USD" and v["gain_usd"] == 3700.0
      and v["applied"] is False, v)
check("3.2 the flag alone flips 'applied'",
      sp.verdict(small, pool, True)["applied"] is True)
check("3.3 already the biggest = 'hold'", sp.verdict(big, pool, True)["would"] == "hold")
check("3.4 a weaker signal is never promoted past the window",
      sp.verdict(result("A/USD", 1000.0, 60.0),
                 {"A/USD": result("A/USD", 1000.0, 60.0),
                  "B/USD": result("B/USD", 9000.0, 60.0 - sp.SPEND_CONF_WINDOW - 5)},
                 True)["would"] == "hold")
check("3.5 a coin with no capacity is not ranked, the verdict says so",
      sp.verdict(result("N/USD", 0.0), {"N/USD": result("N/USD", 0.0)},
                 True)["would"] in ("no_spend", "insufficient"))
check("3.6 a $10 edge is not a different trade (below the min gain)",
      sp.verdict(result("S/USD", 1000.0, 60.0),
                 {"S/USD": result("S/USD", 1000.0, 60.0),
                  "T/USD": result("T/USD", 1010.0, 60.0)}, True)["would"] == "hold")

print("== 4. apply()")
check("4.1 flag off returns the same champion",
      sp.apply(small, pool, config.TradingConfig())[0] is small)
_on = config.TradingConfig()
_on.spend_rank = True
_sw, _rec = sp.apply(small, pool, _on)
check("4.2 flag on swaps to the bigger carry and records the swing",
      _sw is big and _rec["applied"] is True and _rec["cap_usd"] == 4000.0
      and _rec["cap_before_usd"] == 300.0, _rec)

print("== 5. the tournament")
_a, _b = result("AAA/USD", 300.0, 60.0), result("BBB/USD", 4000.0, 60.0)
cfg_off = config.TradingConfig()
cfg_off.mtf_enabled = False
cfg_off.trading_pairs = ["AAA/USD", "BBB/USD"]
cfg_off.deep_scan_enabled = False          # the stub supplies spend itself
sc._evaluate_single = lambda k, p, c: (_a if p == "AAA/USD" else _b)
_ch, _ = sc.tournament_scan(type("K", (), {})(), cfg_off,
                            progress_callback=lambda m: None)
check("5.1 the OFF bracket records the spend verdict every fire",
      isinstance(_ch.signal.spend_rank, dict)
      and _ch.signal.spend_rank["v"] == sp.SPEND_STAMP_VERSION
      and _ch.signal.spend_rank["on"] is False)
check("5.2 ... with the keys a study needs",
      all(kk in _ch.signal.spend_rank for kk in
          ("would", "applied", "cap_usd", "pool")), sorted(_ch.signal.spend_rank))
check("5.3 ... and the champion's own deep read rides along",
      isinstance(_ch.signal.spend_capacity, dict)
      and _ch.signal.spend_capacity.get("cap_usd") in (300.0, 4000.0),
      _ch.signal.spend_capacity)
check("5.4 OFF applied nothing", _ch.signal.spend_rank["applied"] is False)

print("== 6. the fire and the journal")
_orig = bs.CONTEST_WATCH_JSON
_tmp = Path(tempfile.mkdtemp(prefix="spend_stamp_"))
bs.CONTEST_WATCH_JSON = str(_tmp / "contest_watch.json")
try:
    ok = bs.auto_track_popup("SP/USD", 50.0, 300.0, 2.0, 180.0,
                             "BUY 60% (champion)", "champion",
                             setup=bs._setup_tags(route="champion", conviction=60),
                             spend_capacity={"cap_usd": 3266.24, "dd_pct": 3.0,
                                             "dd_src": "volatility_profile"},
                             spend_rank=_ch.signal.spend_rank)
    row = json.loads(Path(bs.CONTEST_WATCH_JSON).read_text())["SP/USD"]
    check("6.1 the watch row carries the read and the decision",
          ok and row["spend_capacity"]["cap_usd"] == 3266.24
          and row["spend_rank"]["would"] in ("prefer", "hold")
          and row.get("spend_v") == sp.SPEND_STAMP_VERSION,
          {kk: row.get(kk) for kk in ("spend_v",)})
    fam = bs._family_fields(row)
    check("6.2 the journal's one writer carries both to the close",
          fam["spend_capacity"]["cap_usd"] == 3266.24
          and fam["spend_rank"]["v"] == sp.SPEND_STAMP_VERSION
          and fam["spend_v"] == sp.SPEND_STAMP_VERSION)
    check("6.3 a pre-field row lands empty and versioned, never broken",
          bs._family_fields({"entry": 1.0})["spend_capacity"] is None
          and bs._family_fields({"entry": 1.0})["spend_v"] == 0)
finally:
    bs.CONTEST_WATCH_JSON = _orig

src_bg = open("background_scanner.py", encoding="utf-8").read()
check("6.4 both fire routes pass the read and the decision",
      src_bg.count('spend_capacity=getattr(signal, "spend_capacity", None)') == 2
      and src_bg.count('spend_rank=getattr(signal, "spend_rank", None)') == 2)
check("6.5 the scanner builds the read at evaluation time",
      "result.spend = _spend.deep_scan(" in open("scanner.py", encoding="utf-8").read())

print("\n".join(lines))
print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
