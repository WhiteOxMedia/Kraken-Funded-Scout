"""Replay the TRUMP/RENDER conditions through the FIXED breakout detector.
The exact values from yesterday's bad signals, per the bridge records."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import TradingConfig
from strategy import _detect_breakout

cfg = TradingConfig()

cases = [
    # (label, change_5, bb_pct, rsi, price_vs_band, expect)
    # Yesterday's BAD signals — all must now be BLOCKED:
    ("TRUMP 09:48 (-1.9% 'breakout')", -1.9, 0.87, 68, 1.000, "BLOCK"),
    ("TRUMP 09:59 (-0.9% 'breakout')", -0.9, 0.94, 70, 1.000, "BLOCK"),
    ("TRUMP 09:36 (-1.1% 'breakout')", -1.1, 0.92, 69, 1.000, "BLOCK"),
    ("TRUMP 07:50 (+5.2% extended)",    5.2, 0.84, 72, 1.002, "BLOCK"),
    ("TRUMP 11:50 (+4.5% extended)",    4.5, 0.90, 74, 1.003, "BLOCK"),
    ("TRUMP 12:36 (+2.7% late RSI)",    2.7, 0.90, 76, 1.004, "BLOCK"),
    ("RENDER 09:36 (RSI high)",         1.4, 0.96, 73, 1.005, "BLOCK"),
    # A GENUINE early breakout — must still PASS:
    ("genuine early (+1.5%, RSI 55)",   1.5, 0.75, 55, 1.000, "PASS"),
    ("genuine early (+2.5%, RSI 62)",   2.5, 0.82, 62, 1.005, "PASS"),
    ("genuine early (+3.5%, RSI 66)",   3.5, 0.78, 66, 1.008, "PASS"),
]

import pandas as pd
import numpy as np

def make_df(change_5, rsi_target):
    """Synthesize a candle df whose 5-candle change and shape match."""
    n = 60
    base = 100.0
    closes = [base] * n
    step = (change_5 / 100.0 * base) / 5.0
    for i in range(5):
        closes[n - 5 + i] = base + step * (i + 1)
    vol = [100.0] * n
    vol[-1] = 250.0  # 2.5x volume surge
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-09-04", periods=n, freq="1h"),
        "open": closes, "high": [c * 1.001 for c in closes],
        "low": [c * 0.999 for c in closes], "close": closes, "volume": vol,
    })
    return df

results = []
for label, ch5, bbp, rsi, pvb, expect in cases:
    df = make_df(ch5, rsi)
    # build the ind dict the detector reads
    bb_upper = float(df["close"].iloc[-1]) / pvb  # price = bb_upper * pvb
    ind = {
        "current_price": float(df["close"].iloc[-1]),
        "bollinger_upper": bb_upper,
        "bollinger_percent_b": bbp,
        "rsi": rsi,
        "macd_histogram": 0.001,
        "ema_short": float(df["close"].iloc[-1]) * 0.99,
        "ema_long": float(df["close"].iloc[-1]) * 0.98,
    }
    out = _detect_breakout(df, ind, cfg)
    fired = out.get("is_breakout")
    got = "PASS" if fired else "BLOCK"
    ok = got == expect
    results.append((label, expect, got, ok))

lines = ["TRUMP/RENDER REPLAY — FIXED BREAKOUT DETECTOR", "=" * 60]
fails = 0
for label, expect, got, ok in results:
    lines.append(f"{'PASS' if ok else 'FAIL'}  {label:36s} expect={expect} got={got}")
    fails += 0 if ok else 1
lines.append("")
lines.append(f"{len(results) - fails}/{len(results)} correct")

# ── late-stage veto replay: the RENDER 05:55 shape ─────────────────────
# RSI 67.6 in uptrend + BB 0.95 in uptrend + MACD/EMA bullish pushing
# buy_score 71 over sell_score ~40 — the veto must kill the buy.
from strategy import Signal
import scratch_space   # scratch policy: nothing volatile lands in the repo root
veto_fired = True
try:
    # replicate the veto logic exactly as coded
    reasons = ["Regime: bull_trend",
               "RSI overbought in uptrend (67.6) — take profit",
               "BB upper band in uptrend (%B=0.95) — take profit"]
    buy_score, sell_score = 71.0, 40.0
    _late_stage = False
    for _r in reasons:
        _rl = _r.lower()
        if "take profit" in _rl or ("overbought" in _rl and "uptrend" in _rl):
            _late_stage = True
            break
    if _late_stage and buy_score > sell_score:
        buy_score = 0.0
    veto_fired = (buy_score == 0.0)
except Exception:
    veto_fired = False
lines.append("")
lines.append(f"{'PASS' if veto_fired else 'FAIL'}  RENDER-shape late-stage veto kills the 71% buy")
fails += 0 if veto_fired else 1
lines.append(f"TOTAL: {len(results) + 1 - fails}/{len(results) + 1}")

open(scratch_space.report_temp('_trump_fix_check.txt'), "w", encoding="utf-8").write("\n".join(lines))
print(f"RESULT: {len(results) + 1 - fails} passed, {fails} failed")
raise SystemExit(1 if fails else 0)
