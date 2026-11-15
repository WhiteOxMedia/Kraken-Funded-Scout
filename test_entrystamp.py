"""THE ENTRY-TIMING STAMP (Nov 15).

The recorded history could not validate any entry-timing gate: the best one
(entry inside the prior hour's cheapest fifth) fitted at +0.59 expectancy on the
tuning half and came back at -1.54 on the held-out half. So no gate ships. What
ships is the measurement — these checks hold the features that a future rule
will be judged on, frozen at the fire:

  * timing_eq: 0.0 at the prior hour's low, 1.0 at its high (the same 0..1 the
    tournament's entry_quality uses, but written down instead of kept in memory);
  * timing_above_low_pct and timing_above_day_low_pct: how much of the move was
    already gone when the fire happened;
  * the forming bar is excluded (no lookahead);
  * fail-soft: no exchange, no candles or a broken read leaves the row unstamped
    and still tracking;
  * mfe_pct rides the close, from the watch row's own running high.

Prints "RESULT: N passed, M failed".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import background_scanner as bs

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


def bars(low=100.0, high=110.0, n=289, first_bar=None, last_bar=None):
    out = []
    for i in range(n):
        lo, hi = low, high
        if i == 0 and first_bar:
            lo, hi = first_bar
        if i == n - 1 and last_bar:
            lo, hi = last_bar
        out.append([1_700_000_000 + i * 300, (lo + hi) / 2, hi, lo, (lo + hi) / 2,
                    1.0])
    return out


class Ex:
    def __init__(self, rows, boom=False):
        self.rows = rows
        self.boom = boom
        self.calls = 0

    def fetch_ohlcv(self, pair, timeframe=None, limit=None):
        self.calls += 1
        if self.boom:
            raise RuntimeError("candles unavailable")
        return self.rows[-int(limit or len(self.rows)):]


print("== 1. the range position itself ==")
mid = bs._entry_timing_stamp("A/USD", 105.0, Ex(bars()))
check("1.1 a fire in the middle of the hour's range reads 0.5",
      mid.get("timing_eq") == 0.5, mid)
low = bs._entry_timing_stamp("A/USD", 100.0, Ex(bars()))
check("1.2 a fire at the hour's low reads 0.0", low.get("timing_eq") == 0.0, low)
high = bs._entry_timing_stamp("A/USD", 110.0, Ex(bars()))
check("1.3 a fire at the hour's high reads 1.0", high.get("timing_eq") == 1.0, high)
check("1.4 beyond the range clamps (never <0 or >1)",
      bs._entry_timing_stamp("A/USD", 99.0, Ex(bars())).get("timing_eq") == 0.0
      and bs._entry_timing_stamp("A/USD", 111.0, Ex(bars())).get("timing_eq") == 1.0)

print("== 2. how much of the move was already gone ==")
check("2.1 above_low_pct is the fire's distance over the hour's low (5.0%)",
      mid.get("timing_above_low_pct") == 5.0, mid.get("timing_above_low_pct"))
day = bars()
day[20][3] = 90.0                     # a low from earlier in the day
d = bs._entry_timing_stamp("A/USD", 105.0, Ex(day))
check("2.2 above_day_low_pct uses the whole day, not just the hour (16.667%)",
      d.get("timing_above_day_low_pct") == 16.667, d.get("timing_above_day_low_pct"))
check("2.3 the day context does not disturb the hour's own reading",
      d.get("timing_eq") == 0.5, d.get("timing_eq"))

print("== 3. no lookahead: the forming bar is ignored ==")
forming = bs._entry_timing_stamp("A/USD", 105.0, Ex(bars(last_bar=(1.0, 2.0))))
check("3.1 a wild FORMING bar does not move the range the fire was judged on",
      forming.get("timing_eq") == 0.5, forming)

print("== 4. fail-soft: the row always lands ==")
check("4.1 no exchange leaves the row unstamped, not broken",
      bs._entry_timing_stamp("A/USD", 105.0, None) == {})
check("4.2 a broken candle read is an empty stamp",
      bs._entry_timing_stamp("A/USD", 105.0, Ex(bars(), boom=True)) == {})
check("4.3 a zero entry is an empty stamp",
      bs._entry_timing_stamp("A/USD", 0.0, Ex(bars())) == {})
check("4.4 too few candles is an empty stamp",
      bs._entry_timing_stamp("A/USD", 105.0, Ex(bars(n=1))) == {})
_ex5 = Ex(bars())
bs._entry_timing_stamp("A/USD", 105.0, _ex5)
check("4.5 one candle read per fire", _ex5.calls == 1, _ex5.calls)

print("== 5. what rides the close ==")
w = {"timing_eq": 0.1, "timing_above_low_pct": 0.5,
     "timing_above_day_low_pct": 3.0, "timing_bars": 288, "high": 106.0}
f = bs._timing_fields(w, 100.0)
check("5.1 the timing features ride the closed row",
      f.get("timing_eq") == 0.1 and f.get("timing_above_day_low_pct") == 3.0, f)
check("5.2 mfe_pct is what the row actually reached (+6.0%)",
      f.get("mfe_pct") == 6.0, f)
check("5.3 an unstamped row carries no timing keys",
      bs._timing_fields({"high": 101.0}, 100.0) == {"mfe_pct": 1.0},
      bs._timing_fields({"high": 101.0}, 100.0))
check("5.4 a row with no high carries no mfe", "mfe_pct" not in bs._timing_fields({}, 100.0))

print("== 6. wiring: stamped at the fire, carried at the close ==")
src = open("background_scanner.py", encoding="utf-8").read()
check("6.1 the watch row is stamped at the fire",
      "**_entry_timing_stamp(pair, entry, exchange)" in src)
check("6.2 the ledger row carries the stamp and the peak",
      "**_timing_fields(w, _entry_x)" in src)
check("6.3 the stamp is computed from 5m candles with the forming bar dropped",
      'exchange.fetch_ohlcv(pair, timeframe="5m"' in src and "candles[:-1]" in src)

print("\n".join(lines))
print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
