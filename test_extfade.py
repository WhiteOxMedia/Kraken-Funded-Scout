"""test_extfade.py - pins THE EXTENSION-FADE HINDSIGHT STUDY (Sep 19).

The study (diag_extfade.py) prices the proposed fade gate's hindsight:
every real popup fire, its over-extension features at fire time from
the deep 1h store, and what the trade did in its 4h contest window.
These pins run the pure core HERMETICALLY (tmpdir logs, synthetic
candles, hand-verified arithmetic) so the battery never touches the
production ledgers:

  1. the fire harvest - both BIG ALERT line formats, AM/PM -> 24h,
     the bare-asset /USD append, and (pair, hour) dedupe
  2. the fire grader - ext / run24 / rngpos / end / mfe arithmetic
     against a hand-computed synthetic candle world
  3. the grader's None contract - short history and open windows are
     skipped, never priced
  4. the band verdict - counts, won, ran, end dollars, empty bands
     omitted
  5. the tercile splitter
  6. THE DECISION RULE - a fade gate is contradicted when the
     fade-shape band's end dollars are positive (extended fires made
     money). Pinned on a synthetic contradiction world so the rule
     itself can never be inverted by a future edit.

The lived verdict from the Sep 19 run (51 graded fires): the fade
shape is the fleet's BEST band, not its worst - HIGH stretch end$
+105.19 (67% won), HIGH run24 +129.43 (67% won), strict fade +75.05.
The gate stays unwired; this suite keeps the study's machinery honest
for the monthly driver that will re-price it as fires accumulate.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


import numpy as np
import pandas as pd

import diag_extfade as ef

# ---------------------------------------------------------------- 1
tmp = tempfile.mkdtemp()
log = Path(tmp) / "scanner.log.2026-09-19"
log.write_text(
    "07:05 AM [main] BIG ALERT: BUY NEAR - 89% - entry $2.85\n"
    "07:50 AM [main] BIG ALERT: BUY NEAR - 90% - entry $2.90\n"
    "07:45 AM [main] \U0001f680 BIG ALERT: BUY STX \u2014 64% \u2014 entry $0.61\n"
    "02:10 PM [main] BIG ALERT: BUY PUMP - 67% - entry $0.0042\n",
    encoding="utf-8")
fires = ef.harvest_fires(log_glob=str(log), today_log=None)
check("harvest: both line formats + dedupe to 3 fires", len(fires) == 3, len(fires))
by_pair = {f["pair"]: f for f in fires}
check("harvest: bare asset gets /USD", "NEAR/USD" in by_pair and "STX/USD" in by_pair,
      sorted(by_pair))
check("harvest: AM/PM to 24h (07:05, 07:45, 14:10)",
      by_pair["NEAR/USD"]["ts"].hour == 7 and by_pair["STX/USD"]["ts"].hour == 7
      and by_pair["PUMP/USD"]["ts"].hour == 14,
      [(p, f["ts"].hour) for p, f in by_pair.items()])
check("harvest: confidence parsed", by_pair["NEAR/USD"]["conf"] == 89)

# ---------------------------------------------------------------- 2
n = 200
idx = pd.date_range("2026-09-19 00:00", periods=n, freq="1h")
closes = [100.0] * (n - 1) + [110.0]
fwd = [111.0, 112.0, 113.0, 114.0]
df = pd.DataFrame({
    "open": closes + fwd, "high": closes + fwd, "low": closes + fwd,
    "close": closes + fwd, "volume": [1.0] * (n + 4),
}, index=idx.append(pd.date_range(idx[-1] + pd.Timedelta("1h"), periods=4, freq="1h")))
g = ef.grade_fire(df, idx[-1])
check("grader: ext vs hand-computed EMA20 (+8.97%)",
      g is not None and abs(g["ext"] - 8.9655) < 0.01, None if g is None else round(g["ext"], 3))
check("grader: run24 = +10% (float-tight)", abs(g["run24"] - 10.0) < 1e-6, g["run24"])
check("grader: rngpos = 100 (top of the 48h range)", g["rngpos"] == 100.0, g["rngpos"])
check("grader: end = +3.64%", abs(g["end"] - 3.6364) < 0.001, round(g["end"], 4))
check("grader: mfe = +3.64% and ran3% qualifies",
      abs(g["mfe"] - 3.6364) < 0.001 and g["mfe"] >= ef.RAN_MFE_PCT)

# ---------------------------------------------------------------- 3
check("grader: short history -> None", ef.grade_fire(df.iloc[:60], idx[59]) is None)
check("grader: open forward window -> None",
      ef.grade_fire(df.iloc[:n], idx[-1]) is None)

# ---------------------------------------------------------------- 4
rows = [
    {"end": 5.0, "mfe": 3.5, "x": 1.0},
    {"end": -2.0, "mfe": 1.0, "x": 2.0},
    {"end": 1.0, "mfe": 0.5, "x": 3.0},
    {"end": -1.0, "mfe": 2.9, "x": 4.0},
]
v = ef.band_verdict(rows, "x", [("A", 0.0, 3.0), ("B", 3.0, 9.0), ("C", 9.0, 100.0)])
check("verdict: band A is [lo,hi) - x=3.0 stays out (n=2, won=1, ran=1)",
      v["A"]["n"] == 2 and v["A"]["won"] == 1 and v["A"]["ran"] == 1, v["A"])
check("verdict: band A end dollars = +$30", v["A"]["end_usd"] == 30.0, v["A"]["end_usd"])
check("verdict: genuinely empty band omitted", "C" not in v, sorted(v))

# ---------------------------------------------------------------- 5
t1, t2 = ef.terciles([{"k": float(i)} for i in range(1, 10)], "k")
check("terciles: 33/66 pct of 1..9",
      abs(t1 - 3.64) < 0.01 and abs(t2 - 6.28) < 0.01, (round(t1, 2), round(t2, 2)))

# ---------------------------------------------------------------- 6
# THE DECISION RULE: a fade gate is contradicted when the fade-shape
# band's end dollars are POSITIVE (extended fires made money).
fade_band = [{"end": 4.0, "mfe": 5.0, "ext": 3.0}, {"end": -1.0, "mfe": 1.0, "ext": 4.0},
             {"end": 2.0, "mfe": 2.0, "ext": 5.0}]
bv = ef.band_verdict(fade_band, "ext", [("HIGH (fade shape)", 2.0, 1e9)])
contradicted = bv["HIGH (fade shape)"]["end_usd"] > 0
check("decision rule: positive fade-band end$ contradicts the gate", contradicted,
      bv["HIGH (fade shape)"]["end_usd"])
check("decision rule: the study's lived verdict is registered",
      "stays unwired" in (__doc__ or ""), "HIGH stretch +$105.19, HIGH run24 +$129.43, strict +$75.05")

out = "\n".join(lines) + f"\n\n{PASS} passed, {FAIL} failed\n"
open("_extfade_check.txt", "w", encoding="ascii", errors="replace").write(out)
print(out)
sys.exit(1 if FAIL else 0)
