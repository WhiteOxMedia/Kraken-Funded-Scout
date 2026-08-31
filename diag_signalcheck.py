"""Signal-check: why is it quiet? Live state of the scanner's funnels —
bridge writes today, lockout, her last words, the current tide, what the
mover funnel sees right now, and whether the top movers would pass its gates."""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

import background_scanner as bs

lines = []


def out(s=""):
    print(s)
    lines.append(s)


# ── 1. bridge signals today (all statuses — written-then-died counts) ──
sc = sqlite3.connect("signals.db")
sc.row_factory = sqlite3.Row
rows = sc.execute("""
    SELECT pair, action, confidence, source, status, timestamp, reason, execution_reason
    FROM signals
    WHERE timestamp >= '2026-08-31'
    ORDER BY timestamp
""").fetchall()
sc.close()
out(f"1. BRIDGE SIGNALS TODAY: {len(rows)}")
for r in rows:
    out(f"   {str(r['timestamp'])[:16]}  {r['pair']:<12} {r['action']:<4} conf {float(r['confidence'] or 0):>3.0f} "
        f"{r['source']:<13} {r['status']:<9} {(r['execution_reason'] or r['reason'] or '')[:60]}")

# ── 2. rotation lockout ───────────────────────────────────────────────
try:
    lock = json.loads(Path("rotation_lockout.json").read_text())
    out(f"\n2. LOCKOUT (fired today): {list(lock.keys()) or 'none'}")
except Exception as exc:
    out(f"\n2. LOCKOUT: unreadable ({exc})")

# ── 3. her last words (scanner liveness) ──────────────────────────────
try:
    vl = [json.loads(x) for x in Path("voice_log.jsonl").read_text().splitlines() if x.strip()]
    out(f"\n3. HER LAST WORDS ({len(vl)} lines logged):")
    for v in vl[-8:]:
        out(f"   {str(v['ts'])[:19]}  {v['text'][:90]}")
except Exception as exc:
    out(f"\n3. voice_log unreadable: {exc}")

# ── 4. the current tide ───────────────────────────────────────────────
tp, ts = bs._fleet_tide()
out(f"\n4. TIDE NOW: {ts} ({tp:.0f}% of the fleet up)" if tp is not None else "\n4. TIDE NOW: unreadable")

# ── 5. what the mover funnel sees right now ───────────────────────────
import ccxt
ex = ccxt.kraken({"enableRateLimit": True})
movers = bs.fetch_kraken_movers(ex)
out(f"\n5. MOVER FUNNEL SEES ({len(movers)} runners >= +5%):")
for m in movers:
    out(f"   {m['pct']:+6.1f}%  {m['pair']:<14} vol ${m['volume'] / 1000:,.0f}K")

# ── 6. dry-run the funnel gates on the top runners ────────────────────
from config import TradingConfig
from strategy import evaluate_strategy

cfg = TradingConfig()
cfg.mtf_enabled = False
cfg.stability_enabled = False


def funnel_check(pair: str):
    """Replicates the funnel's per-pair gates: candle count, evaluate at 55,
    R:R + exhaustion checks. (The move gate + deep read need engine context;
    this covers the first cuts.)"""
    try:
        ohlcv = ex.fetch_ohlcv(pair, timeframe="1h", limit=200)
        if not ohlcv or len(ohlcv) < bs.MOVER_MIN_CANDLES:
            return f"candles {len(ohlcv or [])} < {bs.MOVER_MIN_CANDLES} (fresh listing guard)"
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        cfg.min_confidence = bs.MOVER_MIN_CONF
        sig = evaluate_strategy(df, cfg)
        if sig.action != "buy":
            return f"strategy says {sig.action.upper()} ({sig.confidence:.0f}%)"
        if sig.confidence < bs.MOVER_MIN_CONF:
            return f"conf {sig.confidence:.0f} < {bs.MOVER_MIN_CONF:.0f} floor"
        if not sig.entry_price or not sig.stop_loss or not sig.take_profit:
            return "no entry/sl/tp levels"
        if not sig.risk_reward or sig.risk_reward < 2.0:
            return f"R:R {sig.risk_reward:.1f} < 2.0"
        if any("exhaustion" in r.lower() for r in sig.reasons):
            return "exhaustion in reasons"
        if any("breakout failure" in r.lower() for r in sig.reasons):
            return "breakout failure in reasons"
        return f"SIGNAL WOULD FIRE (conf {sig.confidence:.0f}%)"
    except Exception as exc:
        return f"check error: {exc}"


out(f"\n6. FUNNEL DRY-RUN on the top runners (first cuts only):")
for m in movers[:5]:
    out(f"   {m['pair']:<14} -> {funnel_check(m['pair'])}")

Path("_signalcheck.txt").write_text("\n".join(lines), encoding="utf-8")
print("DONE")
