"""The HYPE sell at 15:34 — exact fill, pnl, and the candle trail around
it. The bot's stop-loss is a POLLING check (positions examined once per
cycle), not a resting exchange order — if HYPE crashed between checks,
the SL fired at wherever price had fallen to, not at -5%."""
import sqlite3
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
MST = ZoneInfo("America/Denver")

# ── 1. the exact close record ────────────────────────────────────────
conn = sqlite3.connect("signals.db")
conn.row_factory = sqlite3.Row
r = dict(conn.execute(
    "SELECT * FROM signals WHERE pair LIKE 'HYPE%' AND status='closed' "
    "ORDER BY closed_at DESC LIMIT 1"
).fetchone())
conn.close()
print("1. THE CLOSE RECORD:")
for k in ("status", "executed_at", "closed_at", "pnl", "execution_reason"):
    print(f"   {k}: {r.get(k)}")

# ── 2. the bot's own trade log for the sell ──────────────────────────
print("2. BOT LOG — the sell itself (15:30-15:40):")
lines = open(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\logs\bot.log",
             encoding="utf-8", errors="replace").read().splitlines()
for l in lines:
    if "2026-08-31 15:3" <= l[:16] <= "2026-08-31 15:40" and any(
            k in l for k in ("ell", "SL", "stop", "Stop", "exit", "order", "fill")):
        print(f"   {l[:155]}")

# ── 3. the candle trail — 1m candles 15:10-15:45 ─────────────────────
import ccxt
ex = ccxt.kraken()
o = ex.fetch_ohlcv("HYPE/USD", timeframe="1m", limit=60)
print("3. HYPE 1m candles 15:10-15:45 (O/H/L/C):")
for c in o:
    t = datetime.fromtimestamp(c[0] / 1000, MST)
    if "15:10" <= t.strftime("%H:%M") <= "15:45":
        bar = "#" * max(1, int((c[2] - c[3]) / 0.5))
        print(f"   {t.strftime('%H:%M')}  O {c[1]:7.2f}  H {c[2]:7.2f}  "
              f"L {c[3]:7.2f}  C {c[4]:7.2f}  {bar}")
