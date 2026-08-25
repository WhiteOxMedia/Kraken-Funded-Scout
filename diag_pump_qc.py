"""Why did the QC say NO to PUMP when the headline said GO?
Replicates the deep scan's exact gates, live."""
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")
now = datetime.now(MST)

import ccxt
ex = ccxt.kraken()
lines = [f"now: {now.strftime('%a %I:%M:%S %p')} Denver", ""]

# 1. the signal the headline used
import sqlite3
conn = sqlite3.connect(BASE / "signals.db")
conn.row_factory = sqlite3.Row
r = conn.execute(
    "SELECT timestamp, confidence, entry_price FROM signals WHERE pair='PUMP/USD' "
    "AND action='buy' ORDER BY timestamp DESC LIMIT 1").fetchone()
conn.close()
sig_ts, sig_conf, sig_entry = r["timestamp"], r["confidence"], r["entry_price"]
live = float(ex.fetch_ticker("PUMP/USD")["last"])
lines.append(f"latest PUMP signal: {sig_ts[:19]} conf={sig_conf:.0f}% entry={sig_entry}")
lines.append(f"live: {live}  (moved {(live/sig_entry-1)*100:+.1f}% since signal)")

# 2. the QC's chase measure — BOTH windows (the QC df is 1h candles!)
try:
    ohlcv = ex.fetch_ohlcv("PUMP/USD", "15m", limit=6)
    low_1h = min(c[3] for c in ohlcv[-4:])
    chase_1h = (live - low_1h) / low_1h * 100
    lines.append(f"intended 1h low (4x15m): {low_1h} -> chase +{chase_1h:.1f}%")
except Exception as exc:
    low_1h, chase_1h = 0, 0.0
    lines.append(f"15m ohlcv failed: {exc}")
try:
    ohlcv_1h = ex.fetch_ohlcv("PUMP/USD", "1h", limit=6)
    low_4h = min(c[3] for c in ohlcv_1h[-4:])
    chase_4h = (live - low_4h) / low_4h * 100
    lines.append(f"ACTUAL QC base (4x1h): {low_4h} -> chase +{chase_4h:.1f}% "
                 f"({'BLOCK' if chase_4h > 2.0 else 'pass'}) — the QC uses this one")
except Exception as exc:
    low_4h, chase_4h = 0, 0.0
    lines.append(f"1h ohlcv failed: {exc}")
chase = chase_4h

# 3. the deep scan's strategy read, live
from config import TradingConfig
from strategy import evaluate_strategy, enable_deep_analysis, disable_deep_analysis
import pandas as pd
cfg = TradingConfig()
try:
    ohlcv_h = ex.fetch_ohlcv("PUMP/USD", timeframe="1h",
                             limit=min(cfg.lookback_candles, 720))
    df = pd.DataFrame(ohlcv_h, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    enable_deep_analysis()
    sig = evaluate_strategy(df, cfg)
    disable_deep_analysis()
    lines.append(f"deep scan read NOW: {sig.action.upper()} {sig.confidence:.0f}% "
                 f"(needs buy + {60} floor)")
    lines.append(f"  reasons: {'; '.join(sig.reasons[:3])[:200]}")
    _read = (sig.action, sig.confidence)
except Exception as exc:
    lines.append(f"strategy eval failed: {exc}")
    _read = None

# 4. pace + payout block
from background_scanner import _hours_to_scalp, _max_resolve_hours
pace = _hours_to_scalp("PUMP/USD", float(live))
cap = _max_resolve_hours()
lines.append(f"pace now: {pace and f'{pace:.1f}h'} (cap {cap:.1f}h) "
             f"({'pass' if pace is None or pace <= cap else 'BLOCK'})")
import app
pb = app._recent_big_payout("PUMP/USD")
lines.append(f"payout block: {'BLOCK $' + str(pb) if pb else 'none'}")

lines.append("")
_v = ("chase guard is the no" if chase > 2.0 else
      ("strategy read flipped" if _read and (_read[0] != "buy" or _read[1] < 60) else
       ("pace" if pace and pace > cap else "payout" if pb else "all gates pass??")))
lines.append("VERDICT: " + _v)

out = BASE / "pump_qc_why.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
