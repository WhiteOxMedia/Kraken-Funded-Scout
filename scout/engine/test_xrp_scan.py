"""What would a Quick Check deep scan say about XRP right now?"""
import sys
from pathlib import Path
import scratch_space   # scratch policy: nothing volatile lands in the repo root

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

lines = []
try:
    from strategy import evaluate_strategy, enable_deep_analysis, disable_deep_analysis
    from config import TradingConfig
    from kraken_api import KrakenAPI
    import keyring
    import pandas as pd

    api_key = keyring.get_password("workshop", "KRAKEN_API_KEY") or ""
    api_secret = keyring.get_password("workshop", "KRAKEN_API_SECRET") or ""
    kr = KrakenAPI(api_key, api_secret)

    cfg = TradingConfig()
    cfg.candle_interval = 60
    cfg.lookback_candles = 200

    enable_deep_analysis()
    ohlcv = kr.fetch_ohlcv("XRP/USD", interval_minutes=60, limit=200)
    disable_deep_analysis()
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)

    sig = evaluate_strategy(df, cfg)
    px = float(df["close"].iloc[-1])
    lines.append(f"XRP live: ${px:.4f}")
    lines.append(f"signal: {sig.action.upper()} {sig.confidence:.0f}%")
    lines.append(f"entry={sig.entry_price} stop={sig.stop_loss} tp={sig.take_profit}")
    # current rotation bar (same source the console uses)
    from background_scanner import get_time_block
    from zoneinfo import ZoneInfo
    from datetime import datetime
    n = datetime.now(ZoneInfo("America/Denver"))
    name, interval, conf, bar, desc, no_buy, key = get_time_block(n.hour, n.weekday())
    lines.append(f"rotation: {name} | bar {bar}% | no_buy={no_buy}")
    lines.append(f"green light (buy >= bar)? {sig.action == 'buy' and sig.confidence >= bar and not no_buy}")
    lines.append("reasons:")
    for r in sig.reasons[:8]:
        lines.append(f"  - {r}")
except Exception as exc:
    lines.append(f"ERROR: {exc}")

out = BASE / "xrp_scan_check.txt"
scratch_space.report_temp('xrp_scan_check.txt').write_text("\n".join(lines), encoding="utf-8")
print("done")
