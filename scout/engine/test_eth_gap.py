"""Why did ETH signal 99% then deep-scan says no? Compare the two contexts."""
import os
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import scratch_space   # scratch policy: nothing volatile lands in the repo root

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")
lines = []

# 1. What did the scanner actually see for ETH?
try:
    from sqlalchemy import create_engine, text
    url = os.environ.get("DB195374BC_DATABASE_URL") or os.environ.get("DB1005AA9E_DATABASE_URL")
    if not url:
        from config import DB_URL
        url = DB_URL
    eng = create_engine(url)
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT timestamp, pair, action, confidence, reasons FROM scout_signals "
            "WHERE pair LIKE 'ETH%' ORDER BY timestamp DESC LIMIT 3")).fetchall()
        lines.append("ETH in scout_signals DB:")
        for r in rows:
            lines.append(f"  {r[0]}  {r[1]}  {r[2]}  {r[3]}%")
            lines.append(f"    reasons: {str(r[4])[:250]}")
except Exception as exc:
    lines.append(f"scout DB: {exc}")

# 2. What does the strategy say RIGHT NOW with the exact same params?
try:
    import ccxt
    import pandas as pd
    from strategy import evaluate_strategy, enable_deep_analysis, disable_deep_analysis
    from config import TradingConfig

    ex = ccxt.kraken({"enableRateLimit": True})
    cfg = TradingConfig()

    # Scanner context: 1h candles, deep analysis OFF for the pre-scan,
    # but ON when the tournament crowned the champion
    lines.append("")
    lines.append("── what a QUICK CHECK deep scan sees (1h, deep analysis ON):")
    enable_deep_analysis()
    ohlcv = ex.fetch_ohlcv("ETH/USD", timeframe="1h", limit=200)
    disable_deep_analysis()
    if ohlcv:
        df = pd.DataFrame(ohlcv, columns=["timestamp","open","high","low","close","volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        sig = evaluate_strategy(df, cfg)
        lines.append(f"  signal: {sig.action.upper()} {sig.confidence:.0f}%")
        for r in sig.reasons[:6]:
            lines.append(f"  - {r}")

    # 3. What the MOVE GATE saw (this is the killer)
    from volatility_engine import get_move_threshold, load_rotation_thresholds
    from background_scanner import rotation_key_for_hour, _schedule_for_dow
    n = datetime.now(MST)
    rot = rotation_key_for_hour(n.hour)
    sched = _schedule_for_dow(n.weekday())
    for level in ("aggressive", "standard"):
        thr = get_move_threshold("ETH/USD", rot, sched, level=level)
        lines.append(f"  move gate ({level}): {thr}% ETH must clear to be tradeable")
    if sig.entry_price and sig.take_profit:
        projected = (sig.take_profit - sig.entry_price) / sig.entry_price * 100
        lines.append(f"  this signal's projected move: {projected:.1f}%")
        lines.append(f"  → {'CLEARS' if projected >= (get_move_threshold('ETH/USD', rot, sched) or 0) else 'BLOCKED BY'} the standard move gate")
except Exception as exc:
    lines.append(f"live scan: {exc}")

out = BASE / "eth_gap_check.txt"
scratch_space.report_temp('eth_gap_check.txt').write_text("\n".join(lines), encoding="utf-8")
print("done")
