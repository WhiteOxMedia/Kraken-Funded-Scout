"""How did the ZEC signal fire? Trace it: DB picks + bridge + scanner trail."""
import os
import sys
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

lines = []

# scout picks DB
try:
    from sqlalchemy import create_engine, text
    url = os.environ.get("DB195374BC_DATABASE_URL") or os.environ.get("DB1005AA9E_DATABASE_URL")
    if not url:
        from config import DB_URL
        url = DB_URL
    eng = create_engine(url)
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT timestamp, pair, action, confidence, entry_price, stop_loss, "
            "take_profit, risk_reward, reasons FROM scout_signals "
            "WHERE pair LIKE 'ZEC%' ORDER BY timestamp DESC LIMIT 3")).fetchall()
        lines.append("ZEC picks in scout_signals DB:")
        for r in rows:
            lines.append(f"  {r[0]}  {r[1]}  {r[2]}  {r[3]}%  entry={r[4]} stop={r[5]} tp={r[6]} rr={r[7]}")
            if r[8]:
                lines.append(f"    reasons: {str(r[8])[:200]}")
except Exception as exc:
    lines.append(f"scout DB: {exc}")

# bridge
try:
    # clock-audit: ok — LIVE-PROBE (manual bot-day report — reports on the live system by design) (clockbombs census 4)
    conn = sqlite3.connect(BASE / "signals.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT timestamp, pair, action, status, source, confidence, entry_price "
        "FROM signals WHERE pair LIKE 'ZEC%' ORDER BY timestamp DESC LIMIT 5").fetchall()
    lines.append("ZEC in bridge (signals.db):")
    for r in rows:
        lines.append(f"  {r['timestamp'][:19]}  {r['pair']}  {r['action']:<4} {r['status']:<9} "
                     f"{r['source']:<14} {r['confidence']}% entry={r['entry_price']}")
    conn.close()
except Exception as exc:
    lines.append(f"bridge: {exc}")

out = BASE / "zec_trace.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
