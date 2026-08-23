"""Diagnostic: why is XRP still held? Entry vs now, trailing high, exit-check math."""
import json
import sqlite3
import time
from pathlib import Path
from zoneinfo import ZoneInfo

MST = ZoneInfo("America/Denver")
BASE = Path(__file__).resolve().parent
lines = []

conn = sqlite3.connect(BASE / "signals.db")
conn.row_factory = sqlite3.Row

# XRP buy signals (filled/closed)
rows = conn.execute(
    "select signal_id, pair, action, status, source, confidence, entry_price, execution_price, "
    "timestamp, executed_at, highest_price from signals where pair like 'XRP%' order by timestamp desc limit 8"
).fetchall()
lines.append("XRP SIGNALS (bridge):")
for r in rows:
    lines.append(f"  {r['timestamp'][:19]}  {r['action']:<4} {r['status']:<9} {r['source']:<16} "
                 f"entry={r['entry_price']} exec={r['execution_price']} high={r['highest_price']}")

# open positions in bridge (status filled buys)
lines.append("")
lines.append("OPEN (filled) POSITIONS in bridge:")
for r in conn.execute("select pair, executed_at, execution_price, highest_price, status from signals where status='filled' and action='buy'"):
    lines.append(f"  {r['pair']}  bought {r['executed_at']} @ {r['execution_price']}  high={r['highest_price']}")

conn.close()

# current price
try:
    import ccxt
    ex = ccxt.kraken({"enableRateLimit": True})
    px = float(ex.fetch_ticker("XRP/USD")["last"])
    lines.append("")
    lines.append(f"XRP LIVE: ${px:.4f}")
    for r in rows:
        if r["status"] == "filled" and r["execution_price"]:
            entry = float(r["execution_price"])
            high = float(r["highest_price"] or 0) or entry
            pnl = (px - entry) / entry * 100
            trail_floor = high * 0.92
            lines.append(f"  vs entry ${entry:.4f}: {pnl:+.2f}% | trailing floor ${trail_floor:.4f} "
                         f"(8% below high ${high:.4f}) → {'TRAIL HIT — should have exited' if px <= trail_floor else 'trail not hit'}")
except Exception as exc:
    lines.append(f"price fetch failed: {exc}")

out = BASE / "xrp_diag_check.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("ok")
