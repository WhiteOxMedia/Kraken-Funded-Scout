"""Would the bounce protocol have caught KII before its 300%+ pump?
Pull KII candles, find the pump start, examine the day before."""
import sys
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

lines = []
try:
    import ccxt
    import pandas as pd
    from strategy import evaluate_strategy
    from config import TradingConfig

    ex = ccxt.kraken({"enableRateLimit": True})
    ohlcv = ex.fetch_ohlcv("KII/USD", timeframe="1h", limit=720)  # Kraken max ~720
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    df["pct"] = df["close"].pct_change() * 100

    # find the pump start: first candle where price jumped > 20%
    pump_start = None
    for i in range(1, len(df)):
        if df["pct"].iloc[i] > 20:
            pump_start = df.index[i]
            break

    lines.append(f"KII now: ${df['close'].iloc[-1]:.6f}")
    if pump_start is None:
        lines.append("no single >20% candle found — pump was gradual")
    else:
        lines.append(f"pump started: {pump_start}")
        pre = df[df.index < pump_start]
        lines.append(f"price before pump: ${pre['close'].iloc[-1]:.6f}")

        # THE DAY BEFORE THE PUMP
        day_before = pre.tail(24)
        day_change = (day_before["close"].iloc[-1] / day_before["close"].iloc[0] - 1) * 100
        vol_24h = (day_before["close"] * day_before["volume"]).sum()
        lines.append("")
        lines.append("── THE DAY BEFORE THE PUMP:")
        lines.append(f"  24h change: {day_change:+.1f}%")
        lines.append(f"  24h volume: ${vol_24h/1e6:.2f}M")
        lines.append(f"  was it a LOSER? {'YES' if day_change < -5 else 'no (not beaten down)'}")
        lines.append(f"  passes $5M volume floor? {'YES' if vol_24h >= 5e6 else 'NO — under the floor'}")

        # would the strategy have said BUY on the day before?
        cfg = TradingConfig()
        cfg.candle_interval = 60
        pre_slice = pre.tail(200) if len(pre) >= 200 else pre
        if len(pre_slice) >= 30:
            sig = evaluate_strategy(pre_slice, cfg)
            lines.append("")
            lines.append("── STRATEGY ON THE DAY BEFORE:")
            lines.append(f"  signal: {sig.action.upper()} {sig.confidence:.0f}%")
            lines.append(f"  entry={sig.entry_price} stop={sig.stop_loss} tp={sig.take_profit}")
            for r in sig.reasons[:6]:
                lines.append(f"    - {r}")
            checklist = (sig.action == "buy" and sig.entry_price and sig.stop_loss
                         and sig.take_profit and sig.risk_reward and sig.risk_reward >= 2.0
                         and not any("exhaustion" in x.lower() for x in sig.reasons)
                         and not any("breakout failure" in x.lower() for x in sig.reasons))
            lines.append(f"  passes checklist: {checklist}")

        # how early would the FAST PATH have caught it? check the pump day itself:
        # first hour where strategy says buy after pump starts
        lines.append("")
        lines.append("── DURING THE PUMP (fast path check):")
        pump_df = df[df.index >= pump_start]
        caught_at = None
        for i in range(2, min(len(pump_df), 24)):
            window = df.iloc[max(0, len(pre) + i - 200):len(pre) + i]
            sig = evaluate_strategy(window, cfg)
            if sig.action == "buy" and sig.confidence >= 50:
                caught_at = (pump_df.index[i], sig.confidence, df["close"].iloc[len(pre) + i])
                break
        if caught_at:
            ts, conf, px = caught_at
            gain_missed = (px / pre['close'].iloc[-1] - 1) * 100
            gain_remaining = (df['close'].iloc[-1] / px - 1) * 100
            lines.append(f"  first BUY signal: {ts} at ${px:.6f} ({conf:.0f}%)")
            lines.append(f"  → that was {gain_missed:+.1f}% into the pump")
            lines.append(f"  → {gain_remaining:+.1f}% was still left after that signal")
        else:
            lines.append("  no BUY signal in the first 24 pump hours (spike blocker held)")

except Exception as exc:
    lines.append(f"ERROR: {exc}")

out = BASE / "kii_woulda.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
