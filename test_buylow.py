"""BUY LOW, SELL HIGH — the ranking, its shadow record, and its flag (Nov 15).

 1. the flag ships OFF and does not disturb the other two rankings
 2. cheapness reads the hour, the day, or both — and refuses when it has neither
 3. the verdict compares near-equal candidates and says prefer / hold /
    insufficient / no_read, listing what it compared
 4. apply() honours only its own flag
 5. the tournament stamps both the score and the decision on every fire
 6. the fire lands them on the watch row and the journal carries them

Prints "RESULT: N passed, M failed".
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import background_scanner as bs
import buy_low as bl
import config
import scanner as sc

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
check("1.1 buy_low_rank exists and is OFF", cfg.buy_low_rank is False, cfg.buy_low_rank)
check("1.2 the two earlier rankings keep their own state",
      cfg.spend_rank is False and cfg.entry_timing_rank is False)
check("1.3 the knobs exist", cfg.buy_low_window > 0 and cfg.buy_low_min_gain > 0,
      (cfg.buy_low_window, cfg.buy_low_min_gain))

print("== 2. cheapness")
c_both = bl.cheapness({"pos_in_day_pct": 10.0, "day_range_pct": 7.0}, entry_quality=0.2)
check("2.1 low in the hour and low in the day reads cheap",
      c_both["cheap"] == 0.85 and c_both["src"] == "hour+day", c_both)
c_ext = bl.cheapness({"pos_in_day_pct": 95.0}, entry_quality=0.95)
check("2.2 extended in both reads expensive", c_ext["cheap"] == 0.05, c_ext)
check("2.3 day only, hour only, and neither",
      bl.cheapness({"pos_in_day_pct": 50.0})["src"] == "day"
      and bl.cheapness({}, entry_quality=0.5)["src"] == "hour"
      and bl.cheapness({}, None) is None)
check("2.4 a missing component is reported null, not defaulted to 'the low'",
      bl.cheapness({"pos_in_day_pct": 40.0}, entry_quality=None)["pos_hour"] is None)
check("2.5 out-of-range inputs are clamped, never inverted",
      0.0 <= bl.cheapness({"pos_in_day_pct": 140.0}, entry_quality=-3)["cheap"] <= 1.0)


def result(pair, conf=60.0, eq=None, spend=None, action="buy"):
    r = sc.ScanResult(pair=pair,
                      signal=sc.Signal(action=action, confidence=conf, price=1.0,
                                       reasons=["r"]),
                      entry_quality=eq)
    r.spend = spend
    return r


print("== 3. the verdict")
ext = result("EXT/USD", 60.0, 0.9, {"pos_in_day_pct": 92.0})
low = result("LOW/USD", 55.0, 0.15, {"pos_in_day_pct": 8.0})
mid = result("MID/USD", 61.0, 0.5, {"pos_in_day_pct": 50.0})
pool = {r.pair: r for r in (ext, low, mid)}
v = bl.verdict(ext, pool, False)
check("3.1 a cheaper near-equal says 'prefer', unapplied while off",
      v["would"] == "prefer" and v["to"] == "LOW/USD" and v["applied"] is False, v)
check("3.2 it lists what it compared, with scores and sources",
      len(v["compared"]) == 3 and v["compared"][0]["pair"] == "LOW/USD"
      and v["compared"][0]["src"] == "hour+day", v["compared"][:1])
check("3.3 the flag alone flips 'applied'",
      bl.verdict(ext, pool, True)["applied"] is True)
check("3.4 already the cheapest = 'hold'", bl.verdict(low, pool, True)["would"] == "hold")
check("3.5 a materially weaker signal is never promoted",
      bl.verdict(result("A/USD", 60.0, 0.9, {"pos_in_day_pct": 90.0}),
                 {"A/USD": result("A/USD", 60.0, 0.9, {"pos_in_day_pct": 90.0}),
                  "B/USD": result("B/USD", 60.0 - bl.BUY_LOW_CONF_WINDOW - 5, 0.1,
                                  {"pos_in_day_pct": 5.0})},
                 True)["would"] == "hold")
check("3.6 no measurement on either side -> 'insufficient'/'no_read'",
      bl.verdict(result("N/USD", 60.0, None, None),
                 {"N/USD": result("N/USD", 60.0, None, None)},
                 True)["would"] in ("insufficient", "no_read"))
check("3.7 a 0.05 edge is not a different entry",
      bl.verdict(result("S/USD", 60.0, 0.50),
                 {"S/USD": result("S/USD", 60.0, 0.50),
                  "T/USD": result("T/USD", 60.0, 0.45)}, True)["would"] == "hold")

print("== 4. apply()")
check("4.1 flag off returns the same champion",
      bl.apply(ext, pool, config.TradingConfig())[0] is ext)
_on = config.TradingConfig()
_on.buy_low_rank = True
_sw, _rec = bl.apply(ext, pool, _on)
check("4.2 flag on swaps to the cheaper coin and records the swing",
      _sw is low and _rec["applied"] is True and _rec["to"] == "LOW/USD"
      and _rec["cheap_before"] == _rec.get("from_cheap"), _rec)

print("== 5. the tournament")
_a = result("AAA/USD", 60.0, 0.9, {"pos_in_day_pct": 92.0})
_b = result("BBB/USD", 60.0, 0.15, {"pos_in_day_pct": 8.0})
cfg_off = config.TradingConfig()
cfg_off.mtf_enabled = False
cfg_off.trading_pairs = ["AAA/USD", "BBB/USD"]
cfg_off.deep_scan_enabled = False
sc._evaluate_single = lambda k, p, c: (_a if p == "AAA/USD" else _b)
_ch, _ = sc.tournament_scan(type("K", (), {})(), cfg_off,
                            progress_callback=lambda m: None)
check("5.1 every fire carries the champion's cheapness",
      isinstance(_ch.signal.buy_low, dict)
      and _ch.signal.buy_low["v"] == bl.BUY_LOW_STAMP_VERSION
      and _ch.signal.buy_low["src"] == "hour+day", _ch.signal.buy_low)
check("5.2 ... and the ranking record, with what it compared",
      isinstance(_ch.signal.buy_low_rank, dict)
      and "compared" in _ch.signal.buy_low_rank
      and _ch.signal.buy_low_rank["on"] is False, _ch.signal.buy_low_rank.get("would"))
check("5.3 OFF applied nothing", _ch.signal.buy_low_rank["applied"] is False)
check("5.4 the ranking runs after the other two (its record names its own flag)",
      _ch.signal.buy_low_rank["flag"] == "buy_low_rank"
      and _ch.signal.spend_rank["flag"] == "spend_rank")

print("== 6. the fire and the journal")
_orig = bs.CONTEST_WATCH_JSON
_tmp = Path(tempfile.mkdtemp(prefix="buylow_stamp_"))
bs.CONTEST_WATCH_JSON = str(_tmp / "contest_watch.json")
try:
    ok = bs.auto_track_popup("BL/USD", 50.0, 300.0, 2.0, 180.0,
                             "BUY 60% (champion)", "champion",
                             setup=bs._setup_tags(route="champion", conviction=60),
                             buy_low={"v": 1, "cheap": 0.82, "src": "hour+day"},
                             buy_low_rank=_ch.signal.buy_low_rank)
    row = json.loads(Path(bs.CONTEST_WATCH_JSON).read_text())["BL/USD"]
    check("6.1 the watch row carries the score and the decision",
          ok and row["buy_low"]["cheap"] == 0.82
          and row["buy_low_rank"]["v"] == bl.BUY_LOW_STAMP_VERSION
          and row.get("buy_low_v") == bl.BUY_LOW_STAMP_VERSION)
    fam = bs._family_fields(row)
    check("6.2 the journal's one writer carries both to the close",
          fam["buy_low"]["cheap"] == 0.82
          and fam["buy_low_rank"] is not None
          and fam["buy_low_v"] == bl.BUY_LOW_STAMP_VERSION)
    check("6.3 a pre-field row lands empty and versioned",
          bs._family_fields({"entry": 1.0})["buy_low"] is None
          and bs._family_fields({"entry": 1.0})["buy_low_v"] == 0)
finally:
    bs.CONTEST_WATCH_JSON = _orig

src_bg = open("background_scanner.py", encoding="utf-8").read()
check("6.4 both fire routes pass the score and the decision",
      src_bg.count('buy_low=getattr(signal, "buy_low", None)') == 2
      and src_bg.count('buy_low_rank=getattr(signal, "buy_low_rank", None)') == 2)
_src_bg = open("background_scanner.py", encoding="utf-8").read()
check("6.6 the LIVE full scan reads deep and runs all three rankings in order",
      "DEEP CHAMPION" in _src_bg
      and _src_bg.index("_spend_mod.deep_scan(") < _src_bg.index("_entry_timing_apply(_champ")
      < _src_bg.index("_spend_mod.apply(_champ") < _src_bg.index("_buy_low_mod.apply(_champ"))
check("6.5 the scanner applies the ranking after the spend one",
      open("scanner.py", encoding="utf-8").read().index("_buy_low.apply(")
      > open("scanner.py", encoding="utf-8").read().index("_spend.apply("))

print("\n".join(lines))
print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
