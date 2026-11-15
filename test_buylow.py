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


print("== 3. the verdict (measured signal, not cheapness)")
ext = result("EXT/USD", 60.0, 0.9, {"pos_in_day_pct": 92.0})   # pos_hour 0.90
low = result("LOW/USD", 55.0, 0.15, {"pos_in_day_pct": 8.0})   # pos_hour 0.15
mid = result("MID/USD", 61.0, 0.5, {"pos_in_day_pct": 50.0})   # pos_hour 0.50
pool = {r.pair: r for r in (ext, low, mid)}
v = bl.verdict(low, pool, False)
check("3.0 the record names the signal that was measured and its study",
      v["signal"] == "pos_hour" and v["signal_dir"] == "+"
      and (v.get("study") or {}).get("verdict") == "accepted"
      and v["study"]["oos"]["ci"][0] > 0
      and v["study"]["baseline"]["clears"] is False,
      (v["signal"], v["study"]["oos"]))
check("3.1 the coin already moving up in its hour is preferred, unapplied while off",
      v["would"] == "prefer" and v["to"] == "EXT/USD" and v["applied"] is False,
      {k: v.get(k) for k in ("would", "to", "gain")})
check("3.2 it lists what it compared, best measured score first",
      len(v["compared"]) == 3 and v["compared"][0]["pair"] == "EXT/USD"
      and v["compared"][0]["score"] == 0.9
      and v["compared"][0]["src"] == "hour+day", v["compared"][:1])
check("3.3 the flag alone flips 'applied'",
      bl.verdict(low, pool, True)["applied"] is True)
check("3.4 already the best score = 'hold'", bl.verdict(ext, pool, True)["would"] == "hold")
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
                  "T/USD": result("T/USD", 60.0, 0.55)}, True)["would"] == "hold")
_hold = bl.verdict(result("S/USD", 60.0, 0.50),
                   {"S/USD": result("S/USD", 60.0, 0.50),
                    "T/USD": result("T/USD", 60.0, 0.55)}, True)
check("3.8 even a hold names the coin it would rather own, the margin and the bar",
      (_hold.get("preferred") or {}).get("pair") == "T/USD"
      and abs((_hold.get("gain_score") or 0) - 0.05) < 1e-6
      and _hold.get("min_gain") == bl.BUY_LOW_MIN_GAIN
      and _hold.get("would_change") is False, _hold)
_pref = bl.verdict(low, pool, True)
check("3.9 a real preference says would_change, with from/to, scores and the gain",
      _pref.get("would_change") is True and _pref.get("to") == "EXT/USD"
      and _pref.get("from") == "LOW/USD"
      and _pref.get("gain") == round(_pref["to_score"] - _pref["from_score"], 4)
      and _pref.get("gain_score") == _pref.get("gain"), _pref)
_check_low = bl.verdict(low, pool, False)
check("3.10 the champion that loses on the measured signal is named as such",
      _check_low["champion"]["pair"] == "LOW/USD"
      and _check_low["preferred"]["pair"] == "EXT/USD"
      and _check_low["champion"]["score"] == 0.15
      and _check_low["preferred"]["score"] == 0.9, _check_low)

print("== 4. apply()")
check("4.1 flag off returns the same champion",
      bl.apply(low, pool, config.TradingConfig())[0] is low)
_on = config.TradingConfig()
_on.buy_low_rank = True
_sw, _rec = bl.apply(low, pool, _on)
check("4.2 flag on swaps to the measured winner and records the swing",
      _sw is ext and _rec["applied"] is True and _rec["to"] == "EXT/USD"
      and _rec["score_before"] == 0.15, _rec)
_sw2, _rec2 = bl.apply(ext, pool, _on)
check("4.3 ... and not when the champion already holds the best score",
      _sw2 is ext and _rec2["applied"] is False, _rec2.get("would"))

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
      "DEEP PASS" in _src_bg and "DEEP CHAMPION" in _src_bg
      and "deep_crown_pool(" in _src_bg.split("def deep_crown_pool")[1]
      and _src_bg.index("_spend_mod.deep_scan(") < _src_bg.index("_entry_timing_apply(top")
      < _src_bg.index("_spend_mod.apply(top") < _src_bg.index("_buy_low_mod.apply(top")
      and "_buys[-6:] if _buys" in _src_bg)
check("6.5 the scanner applies the ranking after the spend one",
      open("scanner.py", encoding="utf-8").read().index("_buy_low.apply(")
      > open("scanner.py", encoding="utf-8").read().index("_spend.apply("))

print("== 7. the deep read reaches any client shape (Nov 15 live bug)")
import spend_capacity as _sp


class _Wrapper:
    """KrakenAPI's shape: the keyword call works."""

    def fetch_ohlcv(self, pair, interval_minutes=60, limit=200):
        return [[i * 3_600_000, 1.0, 1.1, 0.9, 1.05, 5.0] for i in range(40)]


class _Ccxt:
    """A bare ccxt exchange: (symbol, timeframe, since, limit), no kwargs."""

    def __init__(self):
        self.calls = []

    def fetch_ohlcv(self, symbol, timeframe="1m", since=None, limit=200):
        self.calls.append((symbol, timeframe, since, limit))
        return [[i * 3_600_000, 1.0, 1.1, 0.9, 1.05, 5.0] for i in range(40)]


check("7.1 the wrapper shape still works",
      len(_sp._fetch_history(_Wrapper(), "X/USD", 60, 1000) or []) == 40)
_cc = _Ccxt()
_rows = _sp._fetch_history(_cc, "X/USD", 60, 1000)
check("7.2 a ccxt exchange gets the positional call, capped at Kraken's ceiling",
      len(_rows or []) == 40 and _cc.calls and _cc.calls[0][1] == "1h"
      and _cc.calls[0][3] == 720, _cc.calls)


class _Dead:
    def fetch_ohlcv(self, *a, **k):
        raise RuntimeError("down")


check("7.3 a dead client returns None instead of raising",
      _sp._fetch_history(_Dead(), "X/USD", 60, 100) is None)

print("== 8. the live crown pool reads the BUY set, and stamps the signal")
class _DeadEx:
    """No history (every OHLCV fetch fails), but a real ticker: this grades the
    pool and the stamping, not the tape."""
    def fetch_ohlcv(self, *a, **k):
        raise RuntimeError("offline")
    def fetch_ticker(self, *a, **k):
        return {"high": 10.0, "low": 8.0, "last": 9.0, "quoteVolume": 5e6}


_res = {}
for _i in range(7):                      # seven loud HOLDS above one quiet BUY
    _p = f"H{_i}/USD"
    _res[_p] = sc.Signal(action="hold", confidence=60 + _i, price=1.0, reasons=["r"])
_res["Q/USD"] = sc.Signal(action="buy", confidence=20, price=1.0, reasons=["r"])
_bp, _bsig, _cands, _ranks = bs.deep_crown_pool(
    _DeadEx(), _res, {}, config.TradingConfig(), None, None, 50)
check("8.1 the only buy candidate is read even under seven louder holds",
      "Q/USD" in _cands and len(_cands) <= 6, sorted(_cands))
check("8.2 the buy rides with the four loudest non-buys (the comparison set), "
      "and the far tail is not read",
      {"Q/USD", "H3/USD", "H4/USD", "H5/USD", "H6/USD"} <= set(_cands)
      and not ({"H0/USD", "H1/USD"} & set(_cands)), sorted(_cands))

_res2 = {"BUY/USD": sc.Signal(action="buy", confidence=70, price=1.0, reasons=["r"]),
         "HOLD/USD": sc.Signal(action="hold", confidence=72, price=1.0, reasons=["r"])}
_bp2, _bsig2, _c2, _r2 = bs.deep_crown_pool(
    _DeadEx(), _res2, {}, config.TradingConfig(), "BUY/USD", _res2["BUY/USD"], 50)
check("8.3 the crowned buy's signal carries entry_quality for the fire sites",
      hasattr(_bsig2, "entry_quality") and _bsig2.entry_quality == _c2[_bp2].entry_quality,
      _bsig2.entry_quality)
check("8.4 ... and all three records, so the row can never lose them",
      isinstance(_bsig2.spend_capacity, dict) and _bsig2.spend_rank is not None
      and _bsig2.buy_low_rank is not None and _bsig2.buy_low is not None,
      (_bsig2.spend_rank or {}).get("would"))
check("8.5 nothing was applied with every flag off",
      all(not (r or {}).get("applied") for r in _r2.values()))

print("\n".join(lines))
print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
