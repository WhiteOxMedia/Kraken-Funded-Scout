"""Re-score today's calls on PRICE FACTS, not the user's taps. For each
signal: the 90 minutes AFTER it (the out-by window) — max high (MFE), max
low (MAE), whether +0.4% (the $20 on $5,000) was reachable, and where the
coin is now. The user's SOLD-in-2-minutes outcomes are NOT the signal's
verdict — this is."""
import sys
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
MST = ZoneInfo("America/Denver")

import ccxt
ex = ccxt.kraken()

CALLS = [
    # (pair, signal time, entry, source of entry)
    ("HYPE/USD", "10:45", 84.42, "10:45 alert entry"),
    ("ARB/USD", "11:30", 0.0898, "11:30 alert entry"),
    ("BTC/USD", "11:45", 78900.0, "11:45 alert entry"),
    ("JUP/USD", "11:53", 0.2147, "11:53 signal entry"),
    ("HYPE/USD", "12:05", 83.98, "12:06 bot fill"),
    ("POPCAT/USD", "12:37", 0.0531, "12:37 card pick entry"),
]

today = datetime.now(MST).strftime("%Y-%m-%d")
print(f"{'CALL':<11}{'ENTRY':>10}{'$20 AT':>10}{'90m HIGH':>11}{'90m LOW':>10}"
      f"{'$20 HIT?':>10}{'NOW':>9}{'NOW VS':>8}")
print("-" * 82)
for pair, hhmm, entry, src in CALLS:
    sig_t = datetime.fromisoformat(f"{today}T{hhmm}:00").replace(tzinfo=MST)
    try:
        o = ex.fetch_ohlcv(pair, timeframe="5m", limit=200)
        win = [c for c in o
               if sig_t <= datetime.fromtimestamp(c[0] / 1000, MST) <= sig_t + timedelta(minutes=90)]
        if not win:
            print(f"{pair:<11} no candles in window")
            continue
        target = entry * 1.004
        hi = max(c[2] for c in win)
        lo = min(c[3] for c in win)
        hit = hi >= target
        hit_t = next((datetime.fromtimestamp(c[0] / 1000, MST).strftime("%H:%M")
                      for c in win if c[2] >= target), "")
        now_px = float(ex.fetch_ticker(pair).get("last") or 0)
        now_vs = (now_px / entry - 1) * 100
        print(f"{pair:<11}{entry:>10.4f}{target:>10.4f}{hi:>11.4f}{lo:>10.4f}"
              f"{('YES ' + hit_t) if hit else 'no':>10}{now_px:>9.4f}{now_vs:>+7.1f}%")
        # when did the high happen?
        hi_t = datetime.fromtimestamp(
            next(c[0] for c in win if c[2] == hi) / 1000, MST).strftime("%H:%M")
        print(f"{'':<11}  {src} — peak of window at {hi_t}")
    except Exception as e:
        print(f"{pair:<11} failed: {e}")
