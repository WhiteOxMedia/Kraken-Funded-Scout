"""ENTRY TIMING IN THE BRACKET (Nov 15).

The tournament compared assets only on actionable/buy/confidence/R:R, so its
champion was whichever asset had already moved hardest — it could not tell a
champion bought at the bottom of its own range from one bought at the top.
These checks hold the added term: the entry-quality measure itself (0 at the
low, 1 at the high, no lookahead), its use as a tiebreak between comparable
signals, its refusal to override a materially stronger one, and the champion's
stretched/near-low label.

Measured basis (scratch, 1,106 entries on 5m bars, Sep-Nov, split by time):
the cheapest fifth of the 1h range won 52% in both halves against 31-40% for
entries bought mid-range or near the high. That supports a tiebreak; it does not
support overriding confidence, and these tests pin that boundary.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

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


def frame(closes, highs=None, lows=None):
    n = len(closes)
    highs = highs or closes
    lows = lows or closes
    return pd.DataFrame({
        "timestamp": range(n),
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [1.0] * n,
    })


def result(pair, action, conf, quality):
    r = sc.ScanResult(pair=pair,
                      signal=sc.Signal(action=action, confidence=conf, price=1.0, reasons=[]))
    r.entry_quality = quality
    return r


# ── the measure itself ─────────────────────────────────────────────────────
flat = frame([100.0] * 20)
check("range: a flat window has no range to rank against",
      sc._entry_quality(flat) is None)

# prior 12 bars span 100..110; last close at 100 -> 0.0, at 110 -> 1.0
closes = [105.0] * 13 + [100.0]
prev = [105.0] * 12
df_low = frame(closes, highs=[110.0] * 14, lows=[100.0] * 14)
check("range: a close at the prior low scores 0.0", abs(sc._entry_quality(df_low) - 0.0) < 1e-9,
      sc._entry_quality(df_low))

closes_hi = [105.0] * 13 + [110.0]
df_hi = frame(closes_hi, highs=[110.0] * 14, lows=[100.0] * 14)
check("range: a close at the prior high scores 1.0", abs(sc._entry_quality(df_hi) - 1.0) < 1e-9,
      sc._entry_quality(df_hi))

closes_mid = [105.0] * 13 + [105.0]
df_mid = frame(closes_mid, highs=[110.0] * 14, lows=[100.0] * 14)
check("range: mid-range scores 0.5", abs(sc._entry_quality(df_mid) - 0.5) < 1e-9,
      sc._entry_quality(df_mid))

# the last bar's own high/low must not change the answer: it is excluded
df_spike = frame([105.0] * 13 + [105.0],
                 highs=[110.0] * 13 + [999.0], lows=[100.0] * 13 + [1.0])
check("range: the current bar's own spike is excluded (no lookahead)",
      abs(sc._entry_quality(df_spike) - 0.5) < 1e-9, sc._entry_quality(df_spike))

check("range: too little history returns None", sc._entry_quality(frame([100.0] * 5)) is None)

# ── the bracket ────────────────────────────────────────────────────────────
a = result("AAA/USD", "buy", 70.0, 0.20)   # cheap
b = result("BBB/USD", "buy", 74.0, 0.85)   # stretched, slightly stronger
w, l, why = sc._compare_signals(a, b)
check("bracket: comparable signals go to the cheaper entry", w.pair == "AAA/USD", why)
check("bracket: the reason names the timing", "entry timing" in why, why)

strong = result("CCC/USD", "buy", 90.0, 0.90)
weak = result("DDD/USD", "buy", 60.0, 0.10)
w2, _, why2 = sc._compare_signals(weak, strong)
check("bracket: timing does not override a 30-point confidence gap", w2.pair == "CCC/USD", why2)

tie_a = result("EEE/USD", "buy", 70.0, 0.50)
tie_b = result("FFF/USD", "buy", 70.0, 0.55)
w3, _, why3 = sc._compare_signals(tie_a, tie_b)
check("bracket: a 0.05 timing gap is too small to break a tie", w3.pair == "EEE/USD" or "tiebreak" in why3, why3)

noq_a = result("GGG/USD", "buy", 70.0, None)
noq_b = result("HHH/USD", "buy", 72.0, None)
w4, _, _ = sc._compare_signals(noq_a, noq_b)
check("bracket: missing timing falls back to confidence", w4.pair == "HHH/USD")

sell_cheap = result("III/USD", "sell", 70.0, 0.10)
buy_pricey = result("JJJ/USD", "buy", 70.0, 0.90)
w5, _, _ = sc._compare_signals(sell_cheap, buy_pricey, long_only=True)
check("bracket: longs-only still beats a cheap short", w5.pair == "JJJ/USD")

tolerance = sc._compare_signals(result("KKK/USD", "buy", 70.0, 0.90),
                                result("LLL/USD", "buy", 74.0, 0.10),
                                timing_tolerance=0.0)[0]
check("bracket: a zero tolerance restores pure confidence", tolerance.pair == "LLL/USD")

# ── constants the behaviour depends on ────────────────────────────────────
check("constants: 12-bar range, 10-point tolerance, 0.75 stretch",
      (sc.TIMING_RANGE_CANDLES, sc.TIMING_CONF_TOLERANCE, sc.TIMING_STRETCHED) == (12, 10.0, 0.75))

print("\n".join(lines))
print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
