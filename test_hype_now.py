"""HYPE right now: the exact SHOULD I SELL verdict + the $20 math."""
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")

lines = []
try:
    import ccxt
    import pandas as pd
    from strategy import evaluate_strategy
    from config import TradingConfig
    from volatility_engine import get_move_threshold, load_rotation_thresholds
    from background_scanner import rotation_key_for_hour, _schedule_for_dow

    ex = ccxt.kraken({"enableRateLimit": True})
    cfg = TradingConfig()

    ohlcv = ex.fetch_ohlcv("HYPE/USD", timeframe="1h", limit=200)
    df = pd.DataFrame(ohlcv, columns=["timestamp","open","high","low","close","volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    live = float(df["close"].iloc[-1])

    # watch entry
    import json
    w = json.loads((BASE / "contest_watch.json").read_text())
    h = w.get("HYPE/USD", {})
    entry = h.get("entry")
    high = max(h.get("high") or 0, live)
    size = h.get("size") or 5000.0

    lines.append(f"HYPE now: ${live:.4f}")
    if entry:
        usd = (live / entry - 1) * size
        lines.append(f"watch: entry ${entry} · size ${size:,.0f} · P&L ${usd:+,.0f}")
        lines.append(f"trailing high: ${high:.4f} · guard at ${high * 0.96:.4f}")

    sig = evaluate_strategy(df, cfg)
    lines.append(f"strategy: {sig.action.upper()} {sig.confidence:.0f}%")
    for r in sig.reasons[:6]:
        lines.append(f"  - {r}")

    # the verdict logic (contest profile)
    trail_hit = bool(entry and high > entry and live <= high * 0.96)
    sell_thr = 50
    strat_sell = sig.action == "sell" and sig.confidence >= sell_thr
    exh = any("exhaustion" in r.lower() for r in sig.reasons) and sig.action != "buy"
    rider = any("exit signal" in r.lower() for r in sig.reasons)

    lines.append("")
    if trail_hit or strat_sell or exh or rider:
        lines.append("VERDICT: SELL NOW")
        if trail_hit: lines.append("  reason: down 4%+ from the top")
        if strat_sell: lines.append(f"  reason: trend broke ({sig.confidence:.0f}% red ≥ 50%)")
        if exh: lines.append("  reason: move's done")
        if rider: lines.append("  reason: out of steam")
    else:
        lines.append("VERDICT: HOLD")
        gap = sell_thr - sig.confidence if sig.action == "sell" else None
        if gap and gap > 0:
            lines.append(f"  {sig.confidence:.0f}% red · sells at 50% · {gap:.0f} points to go")

    # time to goal
    if sig.take_profit and not (trail_hit or strat_sell or exh or rider):
        remaining = (sig.take_profit - live) / live * 100
        doc = load_rotation_thresholds(max_age_s=300)
        n = datetime.now(MST)
        rot = rotation_key_for_hour(n.hour)
        sched = _schedule_for_dow(n.weekday())
        cell = (doc.get("universe", {}).get("HYPE/USD", {}).get(sched, {}).get(rot)
                or doc.get("median", {}).get(sched, {}).get(rot))
        avg = float(cell.get("avg_move") or 0) if cell else 0
        if avg > 0:
            hours = remaining / avg * 4
            lines.append(f"  to goal {sig.take_profit:.4f}: {remaining:.1f}% left · ~{hours:.0f}h at this pace")

    # the $20 math from entry
    if entry:
        to_20 = 20 - (live / entry - 1) * size
        lines.append(f"")
        lines.append(f"$20 count: {'hit' if to_20 <= 0 else f'${to_20:.0f} to go'}")
except Exception as exc:
    lines.append(f"ERROR: {exc}")

out = BASE / "hype_now.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
