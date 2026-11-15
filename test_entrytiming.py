"""ENTRY TIMING AS A DECISION (Nov 15) — the contract.

The bracket now asks whether a rival entered materially lower in its prior-hour
range at no real cost in signal, and records the answer on every fire. The flag
`entry_timing_rank` decides whether the answer changes who fires; it ships OFF,
so live behaviour is unchanged until the replay's evidence earns it.

These checks hold the mechanism, not a preference for the answer:
  1. the flag exists, defaults off, and off means byte-identical champion choice
  2. the verdict is pure and says hold / prefer / insufficient / no_bracket
  3. on, the bracket may swap; off, it never does
  4. every fire lands the shadow record on the watch row and the journal, with
     the timing_eq fallback filling a missing bracket number
  5. the resolver prefers the real field and falls back honestly

Prints "RESULT: N passed, M failed".
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import background_scanner as bs
import config
import entry_timing as et
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


def result(pair, eq, conf=60.0, action="buy"):
    return sc.ScanResult(pair=pair,
                         signal=sc.Signal(action=action, confidence=conf,
                                          price=1.0, reasons=["r"]),
                         entry_quality=eq)


print("== 1. the flag")
cfg = config.TradingConfig()
check("1.1 entry_timing_rank exists and is OFF by default",
      getattr(cfg, "entry_timing_rank", None) is False, cfg.entry_timing_rank)
check("1.2 the tolerance knobs exist and are not zero",
      cfg.entry_timing_window > 0 and cfg.entry_timing_min_gain > 0,
      (cfg.entry_timing_window, cfg.entry_timing_min_gain))

print("== 2. the verdict is pure and complete")
high, low, mid = result("BBB/USD", 0.90, 60.0), result("AAA/USD", 0.20, 55.0), result("CCC/USD", 0.55, 61.0)
pool = {r.pair: r for r in (high, low, mid)}
v = et.verdict(high, pool, False)
check("2.1 a high champion with a low equal on offer says 'prefer'",
      v["would"] == "prefer" and v["to"] == "AAA/USD" and v["gain"] == 0.7, v)
check("2.2 ... and stays unapplied while the flag is off",
      v["applied"] is False and v["on"] is False)
check("2.3 the flag alone flips 'applied', not the verdict",
      et.verdict(high, pool, True)["applied"] is True
      and et.verdict(high, pool, True)["would"] == "prefer")
check("2.4 already the lowest entry -> 'hold'",
      et.verdict(low, pool, True)["would"] == "hold")
check("2.5 a materially weaker signal is never promoted (20 pts below the window)",
      et.verdict(result("X/USD", 0.95, 60.0),
                 {"X/USD": result("X/USD", 0.95, 60.0),
                  "Y/USD": result("Y/USD", 0.10, 60.0 - et.ENTRY_TIMING_CONF_WINDOW - 5)},
                 True)["would"] == "hold")
check("2.6 a champion with no timing number -> 'insufficient', not a swap",
      et.verdict(result("N/USD", None), {"N/USD": result("N/USD", None), "L/USD": low},
                 True)["would"] == "insufficient")
check("2.7 an empty bracket -> 'no_bracket'",
      et.verdict(result("N/USD", None), {}, False)["would"] == "no_bracket")
check("2.8 the veto is recorded but never applied",
      (lambda r: r["would"] == "hold" and r["veto"] is True and r["applied"] is False)(
          et.verdict(result("V/USD", 0.93), {"V/USD": result("V/USD", 0.93)}, True)))

print("== 3. apply() honours the flag, nothing else")
check("3.1 flag off -> the same champion object comes back",
      et.apply(high, pool, cfg)[0] is high)
_on = config.TradingConfig()
_on.entry_timing_rank = True
_swapped, _rec = et.apply(high, pool, _on)
check("3.2 flag on -> the low rival fires and the record says so",
      _swapped is low and _rec["applied"] is True and _rec["eq"] == 0.2
      and _rec["eq_before"] == 0.9, _rec)

print("== 4. the tournament")
a, b = result("AAA/USD", 0.90, 60.0), result("BBB/USD", 0.20, 60.0)
_eval = {"AAA/USD": a, "BBB/USD": b}


def run(cfgx):
    sc._evaluate_single = lambda k, p, c: (a if p == "AAA/USD" else b)
    return sc.tournament_scan(type("K", (), {})(), cfgx,
                              progress_callback=lambda m: None)[0]


cfg_off = config.TradingConfig()
cfg_off.mtf_enabled = False
cfg_off.trading_pairs = ["AAA/USD", "BBB/USD"]
ch_off = run(cfg_off)
check("4.1 the OFF bracket records a verdict on the champion's signal",
      isinstance(ch_off.signal.entry_timing, dict)
      and ch_off.signal.entry_timing["v"] == et.ENTRY_TIMING_STAMP_VERSION
      and ch_off.signal.entry_timing["on"] is False)
check("4.2 ... and every verdict key a study needs is present",
      all(k in ch_off.signal.entry_timing for k in
          ("would", "applied", "src", "eq", "veto", "pool")),
      sorted(ch_off.signal.entry_timing))
check("4.3 OFF applied nothing", ch_off.signal.entry_timing["applied"] is False)
check("4.4 the flag is not flipped by the tournament", cfg_off.entry_timing_rank is False)

print("== 5. the fire and the journal")
_orig = bs.CONTEST_WATCH_JSON
_tmp = Path(tempfile.mkdtemp(prefix="et_stamp_"))
bs.CONTEST_WATCH_JSON = str(_tmp / "contest_watch.json")
try:
    _verdict = et.verdict(high, pool, False)
    ok = bs.auto_track_popup("ET/USD", 50.0, 300.0, 2.0, 180.0,
                             "BUY 60% (champion)", "champion",
                             setup=bs._setup_tags(route="champion", conviction=60),
                             entry_quality=0.90, entry_timing=_verdict)
    row = json.loads(Path(bs.CONTEST_WATCH_JSON).read_text())["ET/USD"]
    check("5.1 the watch row carries the shadow decision",
          ok and isinstance(row.get("entry_timing"), dict)
          and row["entry_timing"]["would"] == "prefer"
          and row["entry_timing"]["applied"] is False
          and row.get("entry_timing_v") == 1, row.get("entry_timing"))
    fam = bs._family_fields(row)
    check("5.2 the journal's shared writer carries it to the close",
          isinstance(fam.get("entry_timing"), dict)
          and fam["entry_timing"]["would"] == "prefer"
          and fam.get("entry_timing_v") == 1)
    check("5.3 the two numbers travel together (quality + verdict)",
          fam.get("entry_quality") == 0.90 and fam.get("entry_quality_v") == 1)
    ok2 = bs.auto_track_popup("ET2/USD", 10.0, 100.0, 2.0, 180.0,
                              "BUY 60% (mover)", "mover")
    row2 = json.loads(Path(bs.CONTEST_WATCH_JSON).read_text())["ET2/USD"]
    check("5.4 a bracketless route still records a shadow (no_bracket), "
          "not a blank",
          ok2 and row2["entry_timing"]["would"] == "no_bracket"
          and row2["entry_timing"]["applied"] is False, row2.get("entry_timing"))
    check("5.5 a pre-field row lands versioned and empty, never broken",
          bs._family_fields({"entry": 1.0}).get("entry_timing") is None
          and bs._family_fields({"entry": 1.0}).get("entry_timing_v") == 0)
finally:
    bs.CONTEST_WATCH_JSON = _orig

print("== 6. the resolver prefers the real field, falls back honestly")
check("6.1 entry_quality wins when the row is stamped with it",
      et.resolve({"entry_quality": 0.31, "entry_quality_v": 1,
                  "timing_eq": 0.77}) == (0.31, "entry_quality"))
check("6.2 an unstamped entry_quality does not outrank timing_eq",
      et.resolve({"entry_quality": 0.31, "entry_quality_v": 0,
                  "timing_eq": 0.77}) == (0.77, "timing_eq"))
check("6.3 timing_eq covers rows closed before the field existed",
      et.resolve({"timing_eq": 0.12}) == (0.12, "timing_eq"))
check("6.4 a hole returns none, never 0",
      et.resolve({"entry": 1.0}) == (None, "none"))
check("6.5 booleans are not measurements",
      et.resolve({"entry_quality": True, "entry_quality_v": 1}) == (None, "none"))

src_bg = open("background_scanner.py", encoding="utf-8").read()
check("6.6 both fire routes pass the verdict through",
      src_bg.count('entry_timing=getattr(signal, "entry_timing", None)') == 2)
check("6.7 the timing_eq fallback fills a missing number at fire time",
      '"eq_src"] = "timing_eq"' in src_bg)

print("\n".join(lines))
print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
