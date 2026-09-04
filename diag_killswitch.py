"""Kill-switch + balance incident: the circuit breaker says the account
dropped $384.20 -> $294.10 (-23.4%). At the 15:15 audit HYPE was +0.58%.
What happened? HYPE's price, the position record, the bot log since 15:15,
and the account's actual holdings."""
import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
MST = ZoneInfo("America/Denver")

# ── 1. HYPE now ───────────────────────────────────────────────────────
import ccxt
ex = ccxt.kraken()
px = float(ex.fetch_ticker("HYPE/USD").get("last") or 0)
print(f"1. HYPE NOW: ${px:.2f}  (bot entry 83.98 -> {(px/83.98-1)*100:+.1f}%)")

# ── 2. the position record ────────────────────────────────────────────
conn = sqlite3.connect("signals.db")
conn.row_factory = sqlite3.Row
r = conn.execute(
    "SELECT status, executed_at, closed_at, pnl, execution_reason "
    "FROM signals WHERE pair LIKE 'HYPE%' AND status IN ('filled','closed') "
    "ORDER BY timestamp DESC LIMIT 1"
).fetchone()
print(f"2. HYPE RECORD: status {dict(r)['status']}  executed {str(dict(r)['executed_at'])[:16]}  "
      f"closed {dict(r)['closed_at']}  pnl {dict(r)['pnl']}")
conn.close()

# ── 3. the bot log since 15:15 — the whole trail ──────────────────────
print("3. BOT LOG since 15:15 (sells, exits, errors, kill, balance):")
lines = open(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\logs\bot.log",
             encoding="utf-8", errors="replace").read().splitlines()
keys = ("sell", "exit", "SL", "stop", "trail", "error", "ERROR", "kill",
        "KILL", "circuit", "balance", "Position", "TREND", "GET OUT", "WARN")
hits = [l for l in lines if l[:16] >= "2026-08-31 15:15" and any(k in l for k in keys)]
for l in hits[-30:]:
    print(f"   {l[:150]}")

# ── 4. the account's real holdings ───────────────────────────────────
print("4. ACCOUNT (public balance via the bot's own client):")
try:
    sys.path.insert(0, r"C:\Users\birds\Workspace\kraken_xrp_trading_bot")
    from config import TradingConfig
    from kraken_api import KrakenAPI
    k = KrakenAPI(TradingConfig())
    bal = k.fetch_balance()
    total = 0.0
    for cur, b in (bal or {}).items():
        amt = float(b.get("total") or 0)
        if amt <= 0:
            continue
        if cur in ("USD", "ZUSD"):
            val = amt
        else:
            try:
                t = ex.fetch_ticker(f"{cur}/USD")
                val = amt * float(t.get("last") or 0)
            except Exception:
                val = 0.0
        if val > 0.5:
            print(f"   {cur}: {amt:.6f}  (~${val:.2f})")
            total += val
    print(f"   TOTAL: ~${total:.2f}")
except Exception as e:
    print("   balance read failed:", e)
