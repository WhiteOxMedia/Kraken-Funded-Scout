"""Would KII have fired? Deep-scan it now to see what the strategy says."""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

lines = []
try:
    import ccxt
    import pandas as pd
    from strategy import evaluate_strategy, enable_deep_analysis, disable_deep_analysis
    from config import TradingConfig

    ex = ccxt.kraken({"enableRateLimit": True})
    cfg = TradingConfig()
    cfg.candle_interval = 60
    cfg.lookback_candles = 200

    # is KII even tradeable on Kraken?
    try:
        markets = ex.load_markets()
        kii_pairs = [s for s in markets if s.startswith("KII")]
        lines.append(f"KII pairs on Kraken: {kii_pairs}")
    except Exception as exc:
        lines.append(f"market check: {exc}")

    pair = "KII/USD"
    if pair not in (kii_pairs or []):
        pair = "KII/USDT"
    lines.append(f"using pair: {pair}")

    enable_deep_analysis()
    ohlcv = ex.fetch_ohlcv(pair, timeframe="1h", limit=200)
    disable_deep_analysis()
    lines.append(f"candles fetched: {len(ohlcv) if ohlcv else 0}")

    if ohlcv and len(ohlcv) >= 30:
        df = pd.DataFrame(ohlcv, columns=["timestamp","open","high","low","close","volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)

        live = float(df["close"].iloc[-1])
        lines.append(f"KII live: ${live:.6f}")
        lines.append(f"24h change: {(live / float(df['close'].iloc[-25]) - 1) * 100:.1f}%")

        sig = evaluate_strategy(df, cfg)
        lines.append(f"signal: {sig.action.upper()} {sig.confidence:.0f}%")
        lines.append(f"entry={sig.entry_price} stop={sig.stop_loss} tp={sig.take_profit} rr={sig.risk_reward}")
        lines.append(f"reasons:")
        for r in sig.reasons[:8]:
            lines.append(f"  - {r}")

        # the gainer path checklist
        passes = (sig.action == "buy" and sig.entry_price and sig.stop_loss
                  and sig.take_profit and sig.risk_reward and sig.risk_reward >= 2.0
                  and not any("exhaustion" in r.lower() for r in sig.reasons)
                  and not any("breakout failure" in r.lower() for r in sig.reasons))
        lines.append(f"passes gainer checklist: {passes}")
        lines.append(f"≥89% (desktop floor): {sig.confidence >= 89}")
        lines.append(f"≥50% (gainer min_conf): {sig.confidence >= 50}")
        if passes and sig.confidence >= 89:
            lines.append("→ WOULD HAVE POPPED on desktop + Zira")
        elif passes:
            lines.append(f"→ would log to winners pool only (below 89% floor)")
        else:
            lines.append("→ rejected by checklist (no alert at all)")
    else:
        lines.append("not enough data to scan")
except Exception as exc:
    lines.append(f"ERROR: {exc}")

out = BASE / "kii_check.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
